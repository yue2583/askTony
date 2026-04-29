"""根据 last_updated_at 过滤 n 天内有更新的仓库"""
from datetime import datetime, timedelta, timezone
from asktony.cnb_client import CNBClient
from asktony.commands.ingest import _safe_api_call, _repo_key
from asktony.config import load_config


def filter_repos_by_days(n_days: int) -> list[dict]:
    """过滤出最近 n 天内有更新的仓库

    Args:
        n_days: 天数阈值

    Returns:
        最近 n 天内更新的仓库完整信息列表
    """
    cfg = load_config()
    client = CNBClient.from_config(cfg)
    repos = client.get_group_sub_repos(cfg.cnb_group)

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
    print(f"\n最近 {n_days} 天内有更新的仓库 ({len(filtered)} 个):")
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
    import httpx
    from typing import Any

    cfg = load_config()
    client = CNBClient.from_config(cfg)

    # 计算 since 时间
    since = datetime.now(timezone.utc) - timedelta(days=n_days)
    result = []
    for repo in repos:
        repo_commits = []
        repo_id = _repo_key(repo)
        if not repo_id:
            print(f"缺少 repo_id repo={repo}")
            continue

        commits_r = _safe_api_call(
            lambda: client.list_commits(repo_id, since=since),
            label="commits",
            repo=repo_id,
        )

        if commits_r["ok"]:
            commits = commits_r.get("items", [])
            for commit in commits:
                # todo 过滤出大于since的时间，时间形式为："date": "2026-04-29T17:59:54+08:00"
                sha = commit.get("sha")
                p_sha = commits.get("parents", [])
                if len(p_sha) == 2:
                    # 说明是 merge commit
                    continue
                elif len(p_sha) == 1:
                    p_sha = p_sha[0].get("sha")
                    continue
                else:
                    # 为0，说明是初始提交
                    p_sha = ""
                name = commit.get("author", {}).get("name") \
                       or commit.get("committer", {}).get("name") \
                       or commit.get("author", {}).get("username") \
                       or commit.get("committer", {}).get("username")
                email = commit.get("author", {}).get("email") \
                        or commit.get("committer", {}).get("email") \
                        or commit.get("author", {}).get("email") \
                        or commit.get("committer", {}).get("email")
                data = commit.get("author", {}).get("date") \
                       or commit.get("committer", {}).get("date")
                repo_commits.append(
                    (repo_id, name, email, data, sha, p_sha)
                )
        else:
            print(f"\n{repo_id} - 获取失败: {commits_r.get('error', 'unknown error')}")
        result.append(
            (repo_id, repo_commits)
        )
        break


def main():
    repos = filter_repos_by_days(30)
    fetch_and_print_commits(repos, n_days=30, verbose=True)


if __name__ == "__main__":
    main()
