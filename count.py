"""
统计 cnb 提交信息，可指定统计最近 n 天
默认从 data/member_info.xlsx 读取成员信息
默认将结果输出到 data/{n}days_result.xlsx
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from tqdm.auto import tqdm
from asktony.cnb_client import CNBClient
from asktony.commands.ingest import _safe_api_call, _repo_key, _sum_add_del_from_compare
from asktony.config import load_config

cfg = load_config()
client = CNBClient.from_config(cfg)
ex = ThreadPoolExecutor(max_workers=8)

from pathlib import Path

import pandas as pd


def match_and_aggregate(input_path: str | Path, commit_data: list, output_path: str | Path,
                        verbose=False) -> pd.DataFrame:
    """根据 email 匹配 commit 数据，根据email关联full_name，输出统计报表

    Args:
        input_path: 输入 xlsx 路径，包含 full_name, email1, email2, email3 四列
        commit_data: commit 数据列表，格式为 [(repo_id, name, email, date, sha, p_sha, commit_type, additions, deletions, changed_lines, file_count), ...]
        output_path: 输出 xlsx 路径
        verbose: 是否打印详情

    Returns:
        包含 full_name, commit_count, change_lines 的 DataFrame，按 commit_count 降序
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    # 读取输入文件
    df_input = pd.read_excel(input_path)
    # 构建 email -> full_name 的映射（支持多个 email 字段）
    email_to_name: dict[str, str] = {}
    for _, row in df_input.iterrows():
        full_name = row["full_name"]
        for col in ["email1", "email2", "email3"]:
            if col in df_input.columns and pd.notna(row[col]):
                email_to_name[row[col].strip()] = full_name

    # 统计每个 email 对应的 commit_count 和 change_lines
    email_stats: dict[str, dict] = {}
    for item in commit_data:
        repo_id, name, email, date, sha, p_sha, commit_type, additions, deletions, changed_lines, file_count = item
        if not email and verbose:
            print(f"- 缺少 email item={item}")
            continue

        if email not in email_stats:
            email_stats[email] = {"commit_count": 0, "change_lines": 0}
        email_stats[email]["commit_count"] += 1
        # change_lines = additions + deletions（如果为 None 则按 0 计算）
        change_lines_val = changed_lines if changed_lines is not None else (additions or 0) + (deletions or 0)
        email_stats[email]["change_lines"] += change_lines_val

    # email -> stat 构造为 full_name -> stat
    name_stats: dict[str, dict] = {}
    for email, stats in email_stats.items():
        full_name = email_to_name.get(email)
        if not full_name:
            if verbose:
                print(f"- 缺少 full_name email={email}")
            continue
        if full_name in name_stats:
            new_stats = name_stats[full_name]
        else:
            new_stats = {"commit_count": 0, "change_lines": 0}
        new_stats["commit_count"] += stats["commit_count"]
        new_stats["change_lines"] += stats["change_lines"]
        name_stats[full_name] = new_stats
    rows = []
    for name, stats in name_stats.items():
        rows.append({
            "full_name": name,
            "commit_count": stats["commit_count"],
            "change_lines": stats["change_lines"],
        })

    # 构建输出 DataFrame 并按 commit_count 降序排列
    df_output = pd.DataFrame(rows)
    df_output = df_output.sort_values("commit_count", ascending=False).reset_index(drop=True)

    # 输出到 xlsx
    df_output.to_excel(output_path, index=False)
    print(f"已输出到{output_path}")
    return df_output


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


def fetch_commits(since, repo, verbose, observe_member_email, observe_cnb_repo):
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

    logs = {"email_observe": [], "repo_observe": []}

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
            if observe_member_email and email == observe_member_email:
                logs["email_observe"].append(f"{repo_id} - {name} - {email} - {data} - {sha} - {p_sha} - {commit_type}")
            if observe_cnb_repo and repo_id == observe_cnb_repo:
                logs["repo_observe"].append(f"{repo_id} - {name} - {email} - {data} - {sha} - {p_sha} - {commit_type}")
        return repo_id, repo_commits, logs
    else:
        print(f"\n{repo_id} - 获取失败: {commits_r.get('error', 'unknown error')}")
        return repo_id, None, None


def fetch_and_print_commits(repos: list[dict], n_days: int = 30, verbose: bool = False, observe_member_email=None,
                            observe_cnb_repo=None):
    """获取仓库的commits并打印前5条

    Args:
        repos: 仓库完整信息列表
        n_days: 天数阈值，用于计算 since 时间
        verbose: 是否打印详情
        observe_member_email: 观察的成员邮箱
        observe_cnb_repo: 观察的仓库
    """

    # 计算 since 时间
    since = datetime.now(timezone.utc) - timedelta(days=n_days)
    result = []
    futures = []
    logs = {}
    for repo in repos:
        f = ex.submit(fetch_commits, since, repo, verbose, observe_member_email, observe_cnb_repo)
        futures.append(f)
    for fut in tqdm(as_completed(futures), "fetch repo commits"):
        repo_id, repo_commits, _logs = fut.result()
        if _logs:
            for k, v in _logs.items():
                if k not in logs:
                    logs[k] = v
                else:
                    logs[k].extend(v)
        if repo_commits is None:
            continue
        result.append((repo_id, repo_commits))
    for k, v in logs.items():
        msg = "\n".join(v)
        print(f"{k}: \n{msg}")
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
    for fut in tqdm(as_completed(futures), "fill commit stats"):
        results.append(fut.result())

    print(f"成功获取 {len(results)} 条 commit stats")
    return results


def main():
    n_days = 30
    input_path = "data/member_info.xlsx"
    output_path = f"data/{n_days}days_result.xlsx"
    verbose = True
    observe_member_email = None
    # observe_member_email = "gavinwangxin@163.com"
    observe_cnb_repo = None
    # observe_cnb_repo = "clife/computer_vision_beauty/clife-ai-cv-magnifier-analysis"
    repos = filter_repos_by_days(n_days, verbose=verbose)
    repo_commits = fetch_and_print_commits(repos, n_days=n_days, verbose=verbose,
                                           observe_member_email=observe_member_email, observe_cnb_repo=observe_cnb_repo)
    # 以下为统计行数
    stats = add_commit_stats(repo_commits)
    df = match_and_aggregate(input_path, stats, output_path, verbose=verbose)
    pass


if __name__ == "__main__":
    main()
