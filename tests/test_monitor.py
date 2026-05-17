from __future__ import annotations

import json
import subprocess
from pathlib import Path


from hoca.monitor import (
    MonitorEvent,
    MonitorResult,
    check_dangerous_command,
    check_secret_access,
    check_unrelated_directory,
    monitor_process,
    monitor_process_stream,
    save_events,
    save_stop_reason,
)


class TestCheckDangerousCommand:
    def test_rm_rf(self):
        assert check_dangerous_command("rm -rf /some/path") is not None

    def test_rm_Rf(self):
        assert check_dangerous_command("rm -Rf /some/path") is not None

    def test_sudo_rm(self):
        assert check_dangerous_command("sudo rm something") is not None

    def test_chmod_777(self):
        assert check_dangerous_command("chmod 777 file") is not None

    def test_chmod_R_777(self):
        assert check_dangerous_command("chmod -R 777 dir/") is not None

    def test_chown_R(self):
        assert check_dangerous_command("chown -R user:group dir/") is not None

    def test_git_reset_hard(self):
        assert check_dangerous_command("git reset --hard HEAD") is not None

    def test_git_clean_fd(self):
        assert check_dangerous_command("git clean -fd") is not None

    def test_git_push_force(self):
        assert check_dangerous_command("git push --force") is not None

    def test_git_push_f(self):
        assert check_dangerous_command("git push -f") is not None

    def test_gh_pr_merge(self):
        assert check_dangerous_command("gh pr merge 42") is not None

    def test_docker_system_prune(self):
        assert check_dangerous_command("docker system prune") is not None

    def test_brew_uninstall(self):
        assert check_dangerous_command("brew uninstall node") is not None

    def test_git_add_dot(self):
        assert check_dangerous_command("git add .") is not None

    def test_git_add_A(self):
        assert check_dangerous_command("git add -A") is not None

    def test_git_commit_am(self):
        assert check_dangerous_command("git commit -am 'msg'") is not None

    def test_safe_command(self):
        assert check_dangerous_command("npm test") is None

    def test_safe_git_add_file(self):
        assert check_dangerous_command("git add src/main.py") is None

    def test_safe_rm_single_file(self):
        assert check_dangerous_command("rm temp.txt") is None

    def test_safe_git_push_upstream(self):
        assert check_dangerous_command("git push --set-upstream origin feat/branch") is None

    def test_safe_git_push_u(self):
        assert check_dangerous_command("git push -u origin HEAD") is None


class TestCheckSecretAccess:
    def test_env_file(self):
        assert check_secret_access("cat .env", "/project") is not None

    def test_pem_file(self):
        assert check_secret_access("cat server.pem", "/project") is not None

    def test_key_file(self):
        assert check_secret_access("open private.key", "/project") is not None

    def test_kubeconfig(self):
        assert check_secret_access("cat cluster.kubeconfig", "/project") is not None

    def test_safe_file(self):
        assert check_secret_access("cat README.md", "/project") is None

    def test_safe_python(self):
        assert check_secret_access("python main.py", "/project") is None


class TestCheckUnrelatedDirectory:
    def test_home_access(self):
        result = check_unrelated_directory("cd /Users/kevin/other-project", "/project")
        assert result is not None

    def test_etc_access(self):
        result = check_unrelated_directory("cat /etc/passwd", "/project")
        assert result is not None

    def test_project_access(self):
        result = check_unrelated_directory("cd /project/src", "/project")
        assert result is None

    def test_tmp_access_allowed(self):
        result = check_unrelated_directory("cd /tmp/build", "/project")
        assert result is None

    def test_no_path(self):
        result = check_unrelated_directory("echo hello", "/project")
        assert result is None


class TestMonitorEvent:
    def test_to_dict(self):
        event = MonitorEvent(timestamp=1000.0, kind="info", message="test message")
        d = event.to_dict()
        assert d["kind"] == "info"
        assert d["message"] == "test message"
        assert d["timestamp"] == 1000.0


class TestMonitorResult:
    def test_to_dict(self):
        result = MonitorResult(
            exit_code=0,
            stop_reason="completed",
            events=[MonitorEvent(timestamp=1000.0, kind="info", message="ok")],
        )
        d = result.to_dict()
        assert d["exit_code"] == 0
        assert d["stop_reason"] == "completed"
        assert len(d["events"]) == 1


class TestSaveEvents:
    def test_saves_jsonl(self, tmp_path: Path):
        events = [
            MonitorEvent(timestamp=1000.0, kind="info", message="started"),
            MonitorEvent(timestamp=1001.0, kind="exit", message="done"),
        ]
        save_events(tmp_path, events)
        lines = (tmp_path / "monitor-events.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["kind"] == "info"
        assert json.loads(lines[1])["kind"] == "exit"


class TestSaveStopReason:
    def test_saves_json(self, tmp_path: Path):
        save_stop_reason(tmp_path, "timeout", "Hard timeout after 600s")
        data = json.loads((tmp_path / "monitor-stop.json").read_text())
        assert data["stop_reason"] == "timeout"
        assert "Hard timeout" in data["detail"]


class TestMonitorProcess:
    def test_clean_exit(self, tmp_path: Path):
        proc = subprocess.Popen(
            ["printf", "line1\nline2\nline3\n"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = monitor_process(
            proc,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=10,
            stall_seconds=10,
        )
        assert result.exit_code == 0
        assert result.stop_reason == "completed"
        assert (tmp_path / "monitor-events.jsonl").exists()

    def test_dangerous_command_stops(self, tmp_path: Path):
        proc = subprocess.Popen(
            ["printf", "doing work\nrm -rf /\ndone\n"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = monitor_process(
            proc,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=10,
            stall_seconds=10,
        )
        assert result.stop_reason == "dangerous_command"
        assert (tmp_path / "monitor-stop.json").exists()

    def test_secret_access_stops(self, tmp_path: Path):
        proc = subprocess.Popen(
            ["printf", "reading config\ncat .env\n"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = monitor_process(
            proc,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=10,
            stall_seconds=10,
        )
        assert result.stop_reason == "secret_access"

    def test_timeout_stops(self, tmp_path: Path):
        proc = subprocess.Popen(
            ["bash", "-c", "while true; do echo working; sleep 0.01; done"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = monitor_process(
            proc,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=1,
            stall_seconds=60,
        )
        assert result.stop_reason == "timeout"
        assert any(e.kind == "timeout" for e in result.events)


class TestRmRfSafeTargets:
    def test_rm_rf_dist_allowed(self):
        assert check_dangerous_command("rm -rf dist") is None

    def test_rm_rf_dist_slash_allowed(self):
        assert check_dangerous_command("rm -rf dist/") is None

    def test_rm_rf_relative_dist_allowed(self):
        assert check_dangerous_command("rm -rf ./dist") is None

    def test_rm_rf_node_modules_allowed(self):
        assert check_dangerous_command("rm -rf node_modules") is None

    def test_rm_rf_nested_dist_allowed(self):
        assert check_dangerous_command("rm -rf apps/api-gateway/dist") is None

    def test_rm_rf_build_allowed(self):
        assert check_dangerous_command("rm -rf build") is None

    def test_rm_rf_next_allowed(self):
        assert check_dangerous_command("rm -rf .next") is None

    def test_rm_rf_turbo_allowed(self):
        assert check_dangerous_command("rm -rf .turbo") is None

    def test_rm_rf_coverage_allowed(self):
        assert check_dangerous_command("rm -rf coverage") is None

    def test_rm_rf_root_blocked(self):
        assert check_dangerous_command("rm -rf /") is not None

    def test_rm_rf_etc_blocked(self):
        assert check_dangerous_command("rm -rf /etc") is not None

    def test_rm_rf_dot_blocked(self):
        assert check_dangerous_command("rm -rf .") is not None

    def test_rm_rf_dotdot_blocked(self):
        assert check_dangerous_command("rm -rf ..") is not None

    def test_rm_rf_src_blocked(self):
        assert check_dangerous_command("rm -rf src") is not None

    def test_rm_rf_home_blocked(self):
        assert check_dangerous_command("rm -rf ~") is not None

    def test_rm_rf_absolute_path_blocked(self):
        assert check_dangerous_command("rm -rf /usr/local") is not None

    def test_rm_rf_multiple_safe_targets(self):
        assert check_dangerous_command("rm -rf dist node_modules .next") is None

    def test_rm_rf_mixed_safe_and_unsafe_blocked(self):
        assert check_dangerous_command("rm -rf dist src") is not None


class TestEnvExampleNotBlocked:
    def test_env_example_is_safe(self):
        assert check_secret_access("cat apps/api-gateway/.env.example", "/project") is None

    def test_env_example_create_is_safe(self):
        assert check_secret_access("create file apps/api-gateway/.env.example", "/project") is None

    def test_env_local_is_blocked(self):
        assert check_secret_access("cat .env.local", "/project") is not None

    def test_env_production_is_blocked(self):
        assert check_secret_access("cat .env.production", "/project") is not None

    def test_plain_env_still_blocked(self):
        assert check_secret_access("cat .env", "/project") is not None


class TestMonitorProcessStream:
    def test_clean_stream(self, tmp_path: Path):
        import io
        stream = io.StringIO("line1\nline2\nline3\n")
        result = monitor_process_stream(
            stream,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=10,
            stall_seconds=10,
        )
        assert result.exit_code == 0
        assert result.stop_reason == "completed"

    def test_dangerous_command_in_stream(self, tmp_path: Path):
        import io
        stream = io.StringIO("doing work\nrm -rf /\ndone\n")
        result = monitor_process_stream(
            stream,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=10,
            stall_seconds=10,
        )
        assert result.stop_reason == "dangerous_command"
        assert result.exit_code == 1

    def test_safe_rm_rf_in_stream(self, tmp_path: Path):
        import io
        stream = io.StringIO("cleaning\nrm -rf dist\nbuilding\n")
        result = monitor_process_stream(
            stream,
            project_path="/tmp/test",
            run_dir=tmp_path,
            timeout_seconds=10,
            stall_seconds=10,
        )
        assert result.stop_reason == "completed"
        assert result.exit_code == 0
