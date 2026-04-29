"""根据 last_updated_at 过滤 n 天内有更新的仓库"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from asktony.cnb_client import CNBClient
from asktony.commands.ingest import _safe_api_call, _repo_key, _sum_add_del_from_compare
from asktony.config import load_config

cfg = load_config()
client = CNBClient.from_config(cfg)
ex = ThreadPoolExecutor(max_workers=8)

def filter_repos_by_days(n_days: int, verbose: bool = False) -> list[dict]:
    """过滤出最近 n 天内有更新的仓库

    Args:
        n_days: 天数阈值
        verbose: 是否打印详情

    Returns:
        最近 n 天内更新的仓库完整信息列表
    """
    start = time.time()
    repos = client.get_group_sub_repos(cfg.cnb_group)
    print(f"已获取 {len(repos)} 个仓库信息，耗时 {time.time() - start:.2f}s")

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=n_days)

    filtered = []

    for repo in repos:
        web_url = repo.get("web_url", "unknow_web_url")
        if "last_updated_at" not in repo:
            print(f"{web_url} 缺少 last_updated_at 字段")
            continue

        last_updated = datetime.fromisoformat(repo["last_updated_at"].replace("Z", "+00:00"))
        if last_updated >= threshold:
            # 过滤出指定更新时间之后的仓库，保留完整信息
            filtered.append(repo)

    # 打印过滤后的仓库名称
    print(f"最近 {n_days} 天内有更新的仓库 ({len(filtered)} 个):")
    if verbose:
        for repo in filtered:
            print(f"{repo.get('web_url', 'unknow_web_url')}")

    return filtered

def fetch_commits(since, repo, verbose):
    repo_commits = []
    repo_id = _repo_key(repo)
    if not repo_id:
        print(f"缺少 repo_id repo={repo}")

    start = time.time()
    commits_r = _safe_api_call(
        lambda: client.list_commits(repo_id, since=since),
        label="commits",
        repo=repo_id,
        verbose=verbose,
    )

    if commits_r["ok"]:
        commits = commits_r.get("items", [])
        if verbose:
            print(f"已获取 {len(commits)} 个 commit，耗时 {time.time() - start:.2f}s {repo_id} ")
        for commit in commits:
            sha = commit.get("sha")
            p_sha = commit.get("parents", [])
            commit_type = "common"
            if len(p_sha) == 2:
                # 说明是 merge commit，不统计行数
                p_sha = ""
                commit_type = "merge"
            elif len(p_sha) == 1:
                p_sha = p_sha[0].get("sha")
            else:
                # 为0，说明是初始提交
                p_sha = ""
                commit_type = "init"
            name = commit.get("commit", {}).get("author", {}).get("name") \
                   or commit.get("commit", {}).get("committer", {}).get("name") \
                   or commit.get("author", {}).get("username") \
                   or commit.get("committer", {}).get("username")
            email = commit.get("commit", {}).get("author", {}).get("email") \
                    or commit.get("commit", {}).get("committer", {}).get("email") \
                    or commit.get("author", {}).get("email") \
                    or commit.get("committer", {}).get("email")
            data = commit.get("commit", {}).get("author", {}).get("date") \
                   or commit.get("commit", {}).get("committer", {}).get("date")
            repo_commits.append(
                (name, email, data, sha, p_sha, commit_type)
            )
        return repo_id, repo_commits
    else:
        print(f"\n{repo_id} - 获取失败: {commits_r.get('error', 'unknown error')}")
        return repo_id, None


def fetch_and_print_commits(repos: list[dict], n_days: int = 30, verbose: bool = False):
    """获取仓库的commits并打印前5条

    Args:
        repos: 仓库完整信息列表
        n_days: 天数阈值，用于计算 since 时间
        verbose: 是否打印详情
    """

    # 计算 since 时间
    since = datetime.now(timezone.utc) - timedelta(days=n_days)
    result = []
    futures = []
    for repo in repos:
        f = ex.submit(fetch_commits, since, repo, verbose)
        futures.append(f)
    for fut in as_completed(futures):
        repo_id, repo_commits = fut.result()
        if repo_commits is None:
            continue
        result.append((repo_id, repo_commits))
    return result

def compute_one(repo_id: str, commit):
    name, email, data, sha, p_sha, commit_type = commit
    try:
        if commit_type in ("merge", "init"):
            # merge不统计变更行数，没有实际意义代码
            # 初始提交也不统计，compare接口需要base和head
            additions, deletions, changed_lines, file_count = None, None, None, None
        else:
            resp = client.compare_commits(repo_id, p_sha, sha)
            additions, deletions, changed_lines = _sum_add_del_from_compare(resp)
            files = resp.get("files") if isinstance(resp, dict) else None
            file_count = len(files) if isinstance(files, list) else None
    except Exception as e:
        print(f"{repo_id} compare 跳过：error={e}")
        additions, deletions, changed_lines, file_count = None, None, None, None
    return (repo_id,) + commit + (additions, deletions, changed_lines, file_count)

def add_commit_stats(repo_commits: list):
    """为每个 commit 并发查询 additions/deletions/changed_lines 统计

    Args:
        repo_commits: fetch_and_print_commits 返回的结果，格式为 [(repo_id, [(name, email, data, sha, p_sha, commit_type), ...]), ...]
        concurrency: 线程池并发数

    Returns:
        在原有 commit tuple 上追加 (additions, deletions, changed_lines, file_count) 后的列表
    """
    # 构建候选列表：[(repo_id, sha, base_sha, is_merge), ...]
    count = sum(len(commits) for repo_id, commits in repo_commits)
    print(f"共 {count} 个 commit 待查询 stats")

    results = []
    futures = []
    for repo_id, commits in repo_commits:
        for commit in commits:
            f = ex.submit(compute_one, repo_id, commit)
            futures.append(f)
    for fut in as_completed(futures):
        results.append(fut.result())

    print(f"成功获取 {len(results)} 条 commit stats")
    return results


def main():
    n_days = 30
    repos = filter_repos_by_days(n_days, verbose=False)
    repo_commits = fetch_and_print_commits(repos, n_days=n_days, verbose=True)
    stats = add_commit_stats(repo_commits)
    print(stats)


def test_compare_commits():
    repo = "clife/nlp/clife-ai-nlp-agentskill"
    head = "d4aa66026932df516c771671d86d735ed253bf12"
    base = ""
    resp = client.compare_commits(repo, base, head)
    additions, deletions, changed_lines = _sum_add_del_from_compare(resp)
    files = resp.get("files") if isinstance(resp, dict) else None
    file_count = len(files) if isinstance(files, list) else None
    print()

if __name__ == "__main__":
    main()
    # test_compare_commits()
