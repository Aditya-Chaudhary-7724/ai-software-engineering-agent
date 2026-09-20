"""Prompt construction for grounded question answering.

Note on faithfulness (documented honestly rather than solved here):
the prompt instructs the model to answer only from the given context,
but nothing in Phase 4 verifies the model actually complied — it could
still generalize beyond the context or make a claim the context
doesn't support. `Source` citations attached to a `RAGAnswer` are
built directly from retrieval metadata (see context.py), so they are
accurate regardless of what the model's prose says. Measuring
faithfulness of the model's *text* is a Phase 12 evaluation concern.
"""

SYSTEM_PROMPT = (
    "You are a code assistant answering questions about a specific repository. "
    "Answer ONLY using the provided code context. If the context does not contain "
    "enough information to answer, say so explicitly instead of guessing. "
    "Reference specific files and functions/classes by name when relevant."
)


def build_prompt(question: str, context: str) -> str:
    if not context:
        return (
            f"Question: {question}\n\n"
            "No relevant code context was found in the repository for this question."
        )
    return f"Context from the repository:\n\n{context}\n\nQuestion: {question}"
