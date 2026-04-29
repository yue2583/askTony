"""根据 last_updated_at 过滤 n 天内有更新的仓库"""
from datetime import datetime, timedelta, timezone
from asktony.cnb_client import CNBClient
from asktony.config import load_config


def filter_repos_by_days(n_days: int) -> list[dict]:
    """过滤出最近 n 天内有更新的仓库

    Args:
        n_days: 天数阈值

    Returns:
        最近 n 天内更新的仓库列表
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
            # 过滤出指定更新时间之后的仓库
            filtered.append(web_url)

    # 打印过滤后的仓库名称
    print(f"\n最近 {n_days} 天内有更新的仓库 ({len(filtered)} 个):")
    for web_url in filtered:
        print(f"{web_url}")

    return filtered


if __name__ == "__main__":
    filter_repos_by_days(30)
