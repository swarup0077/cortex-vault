# Git cheatsheet

- Undo the last commit but keep the changes: `git reset --soft HEAD~1`
- Throw away local changes to one file: `git restore path/to/file`
- See what changed in the last commit: `git show --stat`
- Start a branch from main: `git switch -c feature/name main`
- Resolve a merge conflict: edit the file, remove the markers, then `git add` it and `git commit`
- Find the commit that broke something: `git bisect start`, then mark commits good or bad
