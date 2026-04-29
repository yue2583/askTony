"""根据 last_updated_at 过滤 n 天内有更新的仓库"""
import time
from datetime import datetime, timedelta, timezone
from asktony.cnb_client import CNBClient
from asktony.commands.ingest import _safe_api_call, _repo_key
from asktony.config import load_config

cfg = load_config()
client = CNBClient.from_config(cfg)


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
    for repo in repos:
        repo_commits = []
        repo_id = _repo_key(repo)
        if not repo_id:
            print(f"缺少 repo_id repo={repo}")
            continue

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
                    # 说明是 merge commit
                    p_sha = p_sha[0].get("sha") + "..." + p_sha[1].get("sha")
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
        else:
            print(f"\n{repo_id} - 获取失败: {commits_r.get('error', 'unknown error')}")
        result.append(
            (repo_id, repo_commits)
        )
    return result


def main():
    repos = filter_repos_by_days(30, verbose=False)
    repo_commits = fetch_and_print_commits(repos, n_days=30, verbose=True)


if __name__ == "__main__":
    main()
