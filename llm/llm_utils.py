#
# Various tools for interacting with LLMs via OpenRouter.
#
# Includes tokenization via models downloaded from Hugging Face.
#

import sys
import json
import pathlib
import logging
import datetime


import stopwatch

_LOGGER = logging.getLogger(__name__)


# Additional logging level for extra info we usually don't even want
# during debugging runs.
DEBUG_EXTRA = 5


class Model:
    def __init__(self, model_id, tokenizer_id, tokenizer_token=None):
        self.summary_cls = OpenRouter

        self.summary_id = model_id
        self.token_id = tokenizer_id
        self.tokenizer_token = tokenizer_token
        self.summary_max = OpenRouter.fetch_context_length(self.summary_id)
        _LOGGER.info(
            f'Context length for "{self.summary_id}" is {self.summary_max} tokens'
        )
        # self.token_max = OpenRouter.fetch_context_length(self.token_id)
        ### gotta figure out to solve:
        ### Token indices sequence length is longer than the specified maximum sequence length for this model (13080 > 8192). Running this sequence through the model will result in indexing errors
        self.token_max = self.summary_max
        _LOGGER.info(f'Context length for "{self.token_id}" is {self.token_max} tokens')

        self.tokenizer = None  # delayed-load

    def ensure_tokenizer(self):
        if self.tokenizer:
            return

        _LOGGER.info(f"Loading <{self.token_id}> tokenizer ...")

        # Note: the import and loading takes a lot of time.

        with stopwatch.Stopwatch("... finished in:"):
            # Delay the import because it is pretty expensive.
            import transformers  # pip install transformers

            self.tokenizer = transformers.AutoTokenizer.from_pretrained(
                # local_files_only=True
                self.token_id,
                token=self.tokenizer_token,
                legacy=False,
            )

        # get_process_memory_info()

    def count_tokens(self, text, what=None):
        "Method to count/approximate tokens for TEXT."

        self.ensure_tokenizer()

        # Note: this is surprisingly fast.
        count = len(self.tokenizer.encode(text))

        what = f" [{what}]" if what else ""
        _LOGGER.debug(
            f"Count{what}: {len(text)} chars, {len(text.split())} words, {count} tokens"
        )
        return count

    def assemble_then_invoke(self, prompt, msgs, system=None):
        "Summarize this chunk of messages."

        if not system:
            system = getattr(self, "SYSTEM_PROMPT", None)

        combined = (
            prompt
            + "\n\n"
            + "\n\n".join(
                f"{m['message_sender_type'].capitalize()}: {m['message_content']}"
                for m in msgs
            )
            + "\n"
        )

        complete = [
            {
                "role": "user",
                "content": combined,
            },
        ]
        if system:
            complete.insert(
                0,
                {
                    "role": "system",
                    "content": system,
                },
            )

        payload = {
            "model": self.summary_id,
            "messages": complete,
        }
        return self.summary_cls.invoke_llm(payload)  # content, reasoning


class ChatSummarize(Model):
    def summarize_chunk(self, msgs):
        "Summarize this chunk of messages."

        prompt = "Summarize the following conversation chunk."
        return self.assemble_then_invoke(prompt, msgs)

    def summarize_recent(self, msgs):
        "Summarize the most recent chunk, in more detail."

        prompt = (
            "Summarize the following conversation chunk."
            " This is the most recent chunk, so provide greater"
            " detail in the Timeline and Key State sections."
        )
        return self.assemble_then_invoke(prompt, msgs)

    def summarize_arc(self, chunks):
        "One overall summary of multiple chunks, forming a story arc."

        prompt = """

Summarize the following conversation chunks into a single cohesive summary, prioritizing details from the most recent chunk (Chunk 3).
Chunk 1: [text]
Chunk 2: [text]
Chunk 3: [text]

        """
        ### this is incorrect.
        return self.assemble_then_invoke(prompt, msgs)

    SYSTEM_PROMPT = """
You are an expert conversation summarizer. Your task is to analyze the
provided conversation chunk(s) and generate a concise, chronological
summary to enable seamless continuation or restart. Structure the
output as follows:

- **Overview**: A 1-2 sentence recap of the chunk’s main theme,
    participants, and current status (e.g., ongoing, resolved,
    paused).
- **Timeline**: A bulleted list of events in chronological order. Each bullet includes:
  - A brief description of the key scene or event.
  - Involved characters or entities.
  - Significant items, decisions, revelations, or changes mentioned.
  - Any unresolved questions or next steps implied.
- **Key State**: A section listing the current state of important
    elements (e.g., character statuses, inventory/items, locations,
    ongoing plots).

**For Individual Chunks**: Summarize the provided chunk factually,
  keeping each summary under 500 words.
**For the Most Recent Chunk**: Provide greater detail in the Timeline
  and Key State sections to emphasize its significance, while
  maintaining the same structure.
**For the Combined Summary**: Integrate all chunk summaries into a
  single cohesive summary, prioritizing details from the most recent
  chunk (e.g., expand its events or highlight its unresolved
  elements). Ensure the combined summary remains under 500 words and
  avoids redundancy.  Stay neutral, factual, and avoid adding new
  information or interpretations. If a chunk lacks timeline elements,
  adapt while maintaining the structure.
"""


class OpenRouter:
    # placeholder for formatter
    HEADERS = {
        "Authorization": f"Bearer {ROUTER_TOKEN}",
        "Content-Type": "application/json",
        "X-Title": "llm_utils",  ### TBD: include script/app name
    }

    URL_CHAT = "https://openrouter.ai/api/v1/chat/completions"
    URL_LIMIT = "https://openrouter.ai/api/v1/auth/key"
    URL_MODELS = "https://openrouter.ai/api/v1/models"

    @classmethod
    @stopwatch.Stopwatch()
    def invoke_llm(cls, payload):
        # Delay import unless/until needed; it takes a while to import.
        import requests

        response = requests.post(
            cls.URL_CHAT,
            json=payload,
            headers=cls.HEADERS,
            timeout=300,
        )
        if response.status_code != 200:
            _LOGGER.error(
                f"LLM error ({response.status_code}):\n  >> BODY: {response.text}"
            )
            return None, response.text
        # response.raise_for_status()

        text = response.text.strip()
        if not text:
            _LOGGER.error("RESPONSE is empty (?)")
            raise Exception("empty response")
        # print('RESPONSE:', type(text))

        return cls.parse(text)  # returns: content, reasoning

    @staticmethod
    def parse(text):
        # This has already been imported, but we need it in our namespace.
        import requests.exceptions

        try:
            j = json.loads(text)
        except requests.exceptions.JSONDecodeError as e:
            _LOGGER.exception(f"JSON error. Body contains: {text.strip()}")
            raise
        except Exception as e:
            _LOGGER.exception("parsing JSON response")
            raise

        choice = j.get("choices", [{}])[0]
        # _LOGGER.debug(f'CHOICE: {choice}')
        msg = choice.get("message", {})
        _LOGGER.log(DEBUG_EXTRA, f"RESPONSE: {msg}")
        # print('KEYS:', msg.keys())

        if usage := j.get("usage"):
            print("USAGE:", usage)

        return msg.get("content", ""), msg.get("reasoning")

    @classmethod
    def fetch_context_length(cls, OR_id):
        """
        Fetch the advertised context length for a specified model from OpenRouter's API.

        Args:
            OR_id (str): The ID of the model (e.g., 'openai/gpt-4o').

        Returns:
            int: The context length of the model.

        Raises:
            requests.RequestException: If the API request fails.
            KeyError: If the model is not found or the response lacks expected data.
        """

        # Delay import unless/until needed; it takes a while to import.
        import requests

        response = requests.get(cls.URL_MODELS, headers=cls.HEADERS)
        response.raise_for_status()
        models = response.json()["data"]

        # print('MODELS:', len(models))
        for model in models:
            if model["id"] == OR_id:
                return model["context_length"]

        raise KeyError(f"Model '{OR_id}' not found.")


def get_sorting_timestamp(timestamps, eps_days=7, min_samples=2):
    """
    Cluster timestamps and return cluster details with noise count.
    Args:
        timestamps: List of Unix timestamps (seconds, int) for messages.
        eps_days: Max gap (days) for messages to be in same cluster.
        min_samples: Min messages for a valid cluster (to ignore pings).
    Returns:
        Tuple: (clusters, noise_count)
        - clusters: List of dicts, each with cluster_id, count, min, median, max (all int).
        - noise_count: Number of noise points (int).
    """
    if not timestamps:
        return [], 0

    # Delay imports. These take a long while.
    if "numpy" not in sys.modules:
        with stopwatch.Stopwatch("imports for chat clustering"):
            import numpy  # pip install numpy
            import sklearn.cluster  # pip install scikit-learn
    else:
        # Bring them into local namespace.
        import numpy
        import sklearn.cluster

    # Convert to numpy array for DBSCAN (2D as required)
    X = numpy.array(timestamps, dtype=float).reshape(-1, 1)

    # DBSCAN: eps in seconds (7 days), min_samples to filter noise
    eps = eps_days * 24 * 60 * 60  # Convert days to seconds
    db = sklearn.cluster.DBSCAN(eps=eps, min_samples=min_samples).fit(X)
    labels = db.labels_  # Cluster labels (-1 for noise)

    # Count noise points (label -1)
    noise_count = numpy.sum(labels == -1)

    # Find valid clusters (exclude noise)
    valid_clusters = set(labels) - {-1}
    clusters = []

    # For each valid cluster, compute stats
    for cluster_id in sorted(valid_clusters):  # Sort for consistent output
        cluster_times = X[labels == cluster_id].flatten()
        clusters.append(
            edict(
                cluster_id=cluster_id,
                count=len(cluster_times),
                min=int(numpy.min(cluster_times)),
                median=int(numpy.median(cluster_times)),
                max=int(numpy.max(cluster_times)),
            )
        )

    return clusters, noise_count


if __name__ == "__main__":
    raise Exception("not designed as a script")
