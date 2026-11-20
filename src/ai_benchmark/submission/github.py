"""The one place ``aicsbench submit`` runs ``git`` and ``gh``.

Tests replace :class:`GitHub` with a fake, so no test touches the network or
the real GitHub CLI. Every command runs without a shell.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_benchmark.errors import SubmissionError


class GitHub:
    """Thin wrapper over the ``git`` and ``gh`` command-line tools."""

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        try:
            proc = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
            )
        except FileNotFoundError:
            raise SubmissionError(f"{args[0]} is not installed") from None
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            words = [a for i, a in enumerate(args) if a != "-c" and args[i - 1 : i] != ["-c"]]
            raise SubmissionError(f"`{' '.join(words[:2])}` failed: {detail}")
        return proc.stdout.strip()

    def logged_in(self) -> bool:
        """True when ``gh`` is installed and ``gh auth status`` succeeds."""
        try:
            self._run(["gh", "auth", "status"])
        except SubmissionError:
            return False
        return True

    def login(self) -> str:
        """The GitHub handle ``gh`` is logged in as."""
        return self._run(["gh", "api", "user", "--jq", ".login"])

    def git_user_name(self) -> str | None:
        try:
            return self._run(["git", "config", "user.name"]) or None
        except SubmissionError:
            return None

    def fork(self, repo: str) -> None:
        """Fork ``repo`` under the logged-in account; a no-op if the fork exists."""
        self._run(["gh", "repo", "fork", repo, "--clone=false"])

    def clone(self, repo: str, dest: Path) -> None:
        """Shallow clone of ``repo``'s default branch into ``dest``."""
        self._run(["gh", "repo", "clone", repo, str(dest), "--", "--depth", "1"])

    def create_branch(self, repo_dir: Path, branch: str) -> None:
        self._run(["git", "checkout", "-b", branch], cwd=repo_dir)

    def commit(self, repo_dir: Path, message: str, author_name: str, author_email: str) -> None:
        """Commit everything in ``repo_dir`` with the submitter as author and committer."""
        self._run(["git", "add", "--all"], cwd=repo_dir)
        self._run(
            [
                "git",
                "-c",
                f"user.name={author_name}",
                "-c",
                f"user.email={author_email}",
                "commit",
                "--quiet",
                "-m",
                message,
            ],
            cwd=repo_dir,
        )

    def push(self, repo_dir: Path, branch: str) -> None:
        """Push ``branch`` to the fork, authenticating through ``gh``."""
        self._run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "push",
                "--quiet",
                "--set-upstream",
                "origin",
                branch,
            ],
            cwd=repo_dir,
        )

    def create_pr(self, repo: str, head: str, title: str, body: str, cwd: Path) -> str:
        """Open a pull request on ``repo`` from ``head`` and return its URL."""
        return self._run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--head",
                head,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=cwd,
        )
