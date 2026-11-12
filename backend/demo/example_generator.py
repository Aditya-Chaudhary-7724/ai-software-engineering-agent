"""Produces pre-generated demo examples with the REAL pipeline.

- Q&A: the same HybridRAGService the Ask route uses (answer + citations).
- Task: the real agent graph, run only up to its PROPOSAL, with an
  in-memory checkpointer that is discarded afterwards. The proposal is
  never approved, so nothing is applied, and no agent_tasks row,
  persistent checkpoint or demo quota is created.

Stored text is redacted (secrets) and server paths are scrubbed, as for
API responses.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from agent.checkpointing import memory_checkpointer
from agent.classification import classify_task
from agent.service import AgentService
from api.trace_sanitizer import scrub_paths
from demo.examples import KIND_ASK, KIND_TASK, MAX_ASK_EXAMPLES, DemoExample
from hybrid.service import HybridRAGService
from observability.recorder import NullRecorder
from observability.redaction import redact_secrets
from observability.tracer import Tracer
from rag.llm.base import LLMProvider
from sandbox.base import TestRunner
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

MAX_PROMPT_CHARS = 2000


class ExampleGenerationError(Exception):
    pass


@dataclass(frozen=True)
class ExampleRequest:
    questions: List[str]
    task_instruction: Optional[str]

    def validate(self) -> None:
        if not 1 <= len(self.questions) <= MAX_ASK_EXAMPLES:
            raise ExampleGenerationError(f"give 1 to {MAX_ASK_EXAMPLES} questions")
        for text in [*self.questions, self.task_instruction or "x"]:
            if not text.strip() or len(text) > MAX_PROMPT_CHARS:
                raise ExampleGenerationError(f"every question/instruction must be 1-{MAX_PROMPT_CHARS} characters")
        if self.task_instruction is not None and classify_task(self.task_instruction) != "modify":
            raise ExampleGenerationError("the task instruction must be a change request (it would not produce a diff)")


def _clean(text: str, root: str) -> str:
    roots = sorted({root, str(Path(root).resolve())}, key=len, reverse=True)
    return redact_secrets(scrub_paths(text, roots))


def generate_examples(
    *,
    request: ExampleRequest,
    repository_id: int,
    root_path: str,
    database_url: str,
    graph_client: Any,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    generator: str,
    test_runner: TestRunner,
) -> List[DemoExample]:
    request.validate()
    examples: List[DemoExample] = []
    rag = HybridRAGService(VectorStore(database_url), graph_client, embedding_provider, llm_provider)
    for position, question in enumerate(request.questions):
        result = rag.answer(question.strip(), repository_id, root_path)
        examples.append(
            DemoExample(
                kind=KIND_ASK, position=position, prompt=question.strip(), generator=generator,
                answer=_clean(result.answer, root_path),
                sources=[
                    {"relative_path": s.relative_path, "start_line": s.start_line, "end_line": s.end_line,
                     "chunk_type": s.chunk_type, "symbol_name": s.symbol_name, "found_via": list(s.found_via)}
                    for s in result.sources
                ],
            )
        )
    if request.task_instruction is not None:
        service = AgentService(
            VectorStore(database_url), graph_client, embedding_provider, llm_provider, test_runner,
            tracer=Tracer(NullRecorder()), checkpointer=memory_checkpointer(),
        )
        run = service.run(request.task_instruction.strip(), repository_id, root_path, thread_id="demo-example")
        message = run.interrupt_message or {}
        if run.status != "awaiting_approval" or not message.get("diff"):
            raise ExampleGenerationError("the task produced no proposed change; try a more specific instruction")
        examples.append(
            DemoExample(
                kind=KIND_TASK, position=0, prompt=request.task_instruction.strip(), generator=generator,
                diff=_clean(message["diff"], root_path), diff_relative_path=message.get("relative_path"),
            )
        )
    return examples
