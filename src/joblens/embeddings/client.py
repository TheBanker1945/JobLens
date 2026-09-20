"""Text -> embedding vectors, via the OpenAI-compatible /embeddings endpoint.

An embedding is a list of numbers (1,024 for qwen3-embedding:0.6b) that places a
text in a "meaning space": texts with similar meaning get nearby vectors. Vectors
from different models live in different spaces, so a query and the documents it is
compared with must always be embedded by the same model.

Search is asymmetric: a short query and a long document are different kinds of
text. Instruction-aware models (like Qwen3-Embedding) expect the query to carry a
task instruction; documents are embedded as they are.
"""

from openai import DefaultHttpxClient, OpenAI

from joblens.config import LLMSettings

Vector = list[float]

DEFAULT_TASK = "Given a job search query, retrieve job vacancies that match it"

# Query templates per verified model; {task} is filled in by the caller. Verified
# 2026-09-19 on Ollama 0.34.2: without it, "Python-ontwikkelaar gezocht in Amsterdam"
# scored a Dutch orderpicker vacancy (0.637) above an English backend developer
# vacancy (0.545) — sentence pattern beat meaning. With it, the ranking was right.
QUERY_TEMPLATES: dict[str, str] = {
    "qwen3-embedding:0.6b": "Instruct: {task}\nQuery: {query}",
}


class EmbeddingClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        batch_size: int = 32,  # texts per request
        http_client: DefaultHttpxClient | None = None,  # tests pass a fake server
    ):
        self.settings = settings
        self.batch_size = batch_size
        self._sdk = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            vectors += self._embed(texts[start : start + self.batch_size])
        return vectors

    def query_text(self, query: str, task: str = DEFAULT_TASK) -> str:
        """The text actually sent for a query: the model's instruction template."""
        template = QUERY_TEMPLATES.get(self.settings.model, "{query}")
        return template.format(task=task, query=query)

    def embed_query(self, query: str, task: str = DEFAULT_TASK) -> Vector:
        return self._embed([self.query_text(query, task)])[0]

    def _embed(self, texts: list[str]) -> list[Vector]:
        response = self._sdk.embeddings.create(model=self.settings.model, input=texts)
        items = list(response.data)
        # OpenAI and Ollama number the items; Gemini leaves index empty and relies on
        # the order. Sort when we can, trust the order when we cannot.
        if all(item.index is not None for item in items):
            items.sort(key=lambda item: item.index)
        if len(items) != len(texts):
            raise ValueError(f"asked for {len(texts)} embeddings, got {len(items)}")
        return [item.embedding for item in items]

    def close(self) -> None:
        self._sdk.close()

    def __enter__(self) -> "EmbeddingClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
