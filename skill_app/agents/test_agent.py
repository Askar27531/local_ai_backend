from __future__ import annotations

import ast
import json
from pathlib import Path

from skill_app.agents.base import AgentContext
from skill_app.config import get_settings
from skill_app.schemas.game_feature import CandidateSpec, CandidateValidation
from skill_app.schemas.model import ModelMessage
from skill_app.schemas.test_report import (
    GeneratedTestPlan,
    GeneratedTestSuite,
    TestExecutionReport,
)
from skill_app.schemas.tool import ToolContext
from skill_app.services import model_gateway
from skill_app.services.skill_loader import load_skill
from skill_app.tools.inputs import RunPytestInput, RunPythonCompileInput
from skill_app.tools.testing import run_pytest, run_python_compile


class TestAgent:
    name = "test_agent"
    skill_name = "test-generator"
    prompt_version = "test-generator-v1"
    max_test_lines = 300
    forbidden_imports = frozenset(
        {
            "httpx",
            "os",
            "requests",
            "shutil",
            "socket",
            "subprocess",
            "urllib",
        }
    )
    forbidden_calls = frozenset(
        {
            "__import__",
            "compile",
            "eval",
            "exec",
            "open",
            "system",
        }
    )

    def execute(
        self,
        *,
        candidate: CandidateSpec,
        workspace_root: Path,
        context: ToolContext,
        static_validation: CandidateValidation,
        agent_context: AgentContext | None = None,
        request: str = "",
        acceptance_criteria: list[str] | None = None,
    ) -> tuple[GeneratedTestPlan, TestExecutionReport, str]:
        test_path = Path(candidate.relative_path).with_name(
            f"test_{Path(candidate.relative_path).stem}.py"
        )
        baseline = GeneratedTestSuite(
            plan=GeneratedTestPlan(
                candidate_path=candidate.relative_path,
                test_path=test_path.as_posix(),
                cases=["candidate imports successfully", "feature payload is enabled"],
                uncovered_risks=[
                    "Engine integration and performance are outside the MVP sandbox."
                ],
            ),
            test_content=self._test_content(candidate),
        )
        suite = baseline
        if agent_context is not None:
            skill = load_skill(self.skill_name)
            profile = model_gateway.get_profile(get_settings().model_profile)
            user_content = baseline.model_dump_json()
            if profile.provider != "fake":
                user_content = json.dumps(
                    {
                        "request": request,
                        "acceptance_criteria": acceptance_criteria or [],
                        "candidate": candidate.model_dump(mode="json"),
                        "static_validation": static_validation.model_dump(mode="json"),
                        "required_test_path": test_path.as_posix(),
                        "baseline_shape_example": baseline.model_dump(mode="json"),
                        "instruction": (
                            "Generate semantic tests for the request and acceptance criteria. "
                            "The baseline is only a schema and import-pattern example; "
                            "do not limit coverage to the generic build function."
                        ),
                    },
                    ensure_ascii=False,
                )
            suite = model_gateway.invoke_structured(
                agent_context.db,
                task_id=agent_context.task.id,
                step_run_id=agent_context.step_run.id,
                profile_name=get_settings().model_profile,
                messages=[
                    ModelMessage(
                        role="system",
                        content=f"{_load_prompt()}\n\nTesting skill:\n{skill.body}",
                    ),
                    ModelMessage(role="user", content=user_content),
                ],
                response_model=GeneratedTestSuite,
                prompt_version=self.prompt_version,
            )
            if profile.provider != "fake" and suite == baseline:
                raise ValueError("Model returned the unchanged deterministic test baseline.")
        self.validate_suite(suite, candidate, test_path)
        plan = suite.plan
        test_content = suite.test_content
        target = workspace_root / test_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test_content, encoding="utf-8")
        compile_result = run_python_compile(
            context,
            RunPythonCompileInput(paths=[candidate.relative_path, test_path.as_posix()]),
        )
        pytest_result = run_pytest(
            context,
            RunPytestInput(paths=[test_path.as_posix()], max_failures=1),
        )
        errors = list(static_validation.errors)
        if not compile_result.ok:
            errors.append("Python compilation failed.")
        if not pytest_result.ok:
            errors.append("Generated pytest failed.")
        report = TestExecutionReport(
            passed=static_validation.valid and compile_result.ok and pytest_result.ok,
            compile_passed=compile_result.ok,
            pytest_passed=pytest_result.ok,
            candidate_path=candidate.relative_path,
            test_path=test_path.as_posix(),
            stdout=pytest_result.stdout,
            stderr="\n".join(filter(None, [compile_result.stderr, pytest_result.stderr])),
            errors=errors,
            uncovered_risks=plan.uncovered_risks,
        )
        return plan, report, test_content

    def validate_suite(
        self,
        suite: GeneratedTestSuite,
        candidate: CandidateSpec,
        expected_test_path: Path,
    ) -> None:
        if suite.plan.candidate_path != candidate.relative_path:
            raise ValueError("Generated test candidate_path does not match the candidate.")
        if suite.plan.test_path != expected_test_path.as_posix():
            raise ValueError("Generated test_path does not match the managed test path.")
        if len(suite.test_content.splitlines()) > self.max_test_lines:
            raise ValueError(f"Generated tests exceed {self.max_test_lines} lines.")
        try:
            tree = ast.parse(suite.test_content)
        except SyntaxError as exc:
            raise ValueError(f"Generated tests contain invalid Python: {exc.msg}.") from exc
        test_functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        if not test_functions:
            raise ValueError("Generated tests must contain at least one test function.")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                denied = imported & self.forbidden_imports
                if denied:
                    raise ValueError(f"Generated tests use forbidden imports: {sorted(denied)}.")
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = node.module.split(".", 1)[0]
                if imported in self.forbidden_imports:
                    raise ValueError(f"Generated tests use forbidden import: {imported}.")
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in self.forbidden_calls:
                    raise ValueError(f"Generated tests use forbidden call: {name}.")

    def _test_content(self, candidate: CandidateSpec) -> str:
        if "FastAPI(" in candidate.content and "app =" in candidate.content:
            return (
                "import importlib.util\n"
                "from pathlib import Path\n"
                "from fastapi.testclient import TestClient\n\n"
                f'CANDIDATE = Path(__file__).with_name("{Path(candidate.relative_path).name}")\n'
                'spec = importlib.util.spec_from_file_location("candidate_module", CANDIDATE)\n'
                "module = importlib.util.module_from_spec(spec)\n"
                "assert spec.loader is not None\n"
                "spec.loader.exec_module(module)\n\n"
                "def test_health_endpoint():\n"
                "    response = TestClient(module.app).get('/health')\n"
                "    assert response.status_code == 200\n"
            )
        function_name = next(
            (
                line.split("def ", 1)[1].split("(", 1)[0]
                for line in candidate.content.splitlines()
                if line.startswith("def build_")
            ),
            "build_feature",
        )
        return (
            "import importlib.util\n"
            "from pathlib import Path\n\n"
            f'CANDIDATE = Path(__file__).with_name("{Path(candidate.relative_path).name}")\n'
            'spec = importlib.util.spec_from_file_location("candidate_module", CANDIDATE)\n'
            "module = importlib.util.module_from_spec(spec)\n"
            "assert spec.loader is not None\n"
            "spec.loader.exec_module(module)\n\n"
            "def test_feature_payload_is_enabled():\n"
            f"    payload = module.{function_name}()\n"
            '    assert payload["enabled"] is True\n'
            '    assert payload["feature_id"]\n'
        )


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _load_prompt() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "test_generator"
        / "v1.md"
    ).read_text(encoding="utf-8")
