"""
将 dim_member_filled 的 full_name 映射到近一月 commits 统计，
筛选 full_name 不为空且不含部门列，输出到 result.csv
"""
import pandas as pd

commits_df = pd.read_csv("./output/近一月commits统计.csv")
members_df = pd.read_csv("./data/dim_member_filled.csv")

# 用 email 匹配，失败则用 email_aliases 匹配（alias 去重避免重复索引）
email_map = members_df.set_index("email")["full_name"]
alias_map = members_df.dropna(subset=["email_aliases"]).drop_duplicates(subset="email_aliases").set_index("email_aliases")["full_name"]
commits_df["full_name"] = commits_df["author_email"].map(email_map)
missing = commits_df["full_name"].isna()
commits_df.loc[missing, "full_name"] = commits_df.loc[missing, "author_email"].map(alias_map)

# 筛选 full_name 不为空，去掉 department 列
result = commits_df[commits_df["full_name"].notna()].drop(
    columns=["department_level2_name", "department_level3_name"]
)
cols = result.columns.tolist()
for col in ["full_name", "commit_count", "changed_lines"]:
    cols.remove(col)
result = result[["full_name", "commit_count", "changed_lines"] + cols]

# 重命名前三列为中文
result.rename(columns={
    "full_name": "姓名",
    "commit_count": "提交次数",
    "changed_lines": "变更行数",
}, inplace=True)

result.to_csv("output/result.csv", index=False)
print(f"共 {len(result)} 行已写入 output/result.csv")
