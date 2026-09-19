#!/bin/sh
# 给 git 历史里所有路径加上 server/ 前缀。
#
# 原来的仓库只有 server/ 这一块（E:\code\zcsmw\server\.git，47 个提交），
# 现在要把仓库提到项目根、让那些提交落在 server/ 下面，所以得重写一遍路径。
#
# 用法（由 filter-branch 逐个提交调用，$GIT_INDEX_FILE 是它给的临时索引）：
#   git filter-branch --prune-empty --index-filter 'sh _prefix.sh' -- --all
#
# `git ls-files -s` 的行是 `<mode> <sha> <stage>\t<path>`，
# 在制表符后面插 `server/` 就完成了重写。
git ls-files -s \
  | sed 's-\t-&server/-' \
  | GIT_INDEX_FILE="$GIT_INDEX_FILE.new" git update-index --index-info \
  && mv "$GIT_INDEX_FILE.new" "$GIT_INDEX_FILE"
