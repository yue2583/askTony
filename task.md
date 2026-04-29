模仿ingest.py的
【
commits_r = _safe_api_call(
            lambda: client.list_commits(repo_id, since=since), label="commits", repo=repo_id, verbose=verbose
        )
】
代码，调整tmp.py，filter_repos_by_days逻辑不变，仅改变返回为repo的完整信息，
然后编写和调用第二个方法：传入repos，用第一个获取commits，打印前5个commits信息