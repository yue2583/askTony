阅读和理解【src/asktony/commands/ingest.py:407】和【tmp.main】

参考前者的逻辑，在后者新增方法：创建线程池，并发查询获取commit的新增，删除，变更数量增加到repo_commits的每个commit上去。
打印返回值。
不运行脚本，我来运行