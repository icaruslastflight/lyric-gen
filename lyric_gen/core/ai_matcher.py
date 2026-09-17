"""Optional Claude AI matcher — resolves remix/remake titles to originals for lyric lookup."""

import logging

logger = logging.getLogger("lyricgen.ai")


class AIMatcher:
    """Uses Claude API to intelligently map remix/remake titles to original song names.

    Example: "Kernkraft 400 (DJ Isaac Hardstyle Remix)" -> "Kernkraft 400"

    Only called when LRCLIB fuzzy matching returns no results. Falls back
    gracefully if no API key is configured or if Claude is unreachable.
    """

    def __init__(self, prefs):
        self.prefs = prefs
        self._api_key = prefs["ai"].get("anthropic_key", "")
        self._enabled = prefs["ai"].get("enabled", False) and bool(self._api_key)
        self._client = None

    @property
    def available(self):
        return self._enabled

    def _get_client(self):
        if self._client is None and self._enabled:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                logger.debug("anthropic package not installed — AI matching disabled")
                self._enabled = False
            except Exception as e:
                logger.warning("Failed to init Anthropic client: %s", e)
                self._enabled = False
        return self._client

    def resolve_title(self, title, artist=""):
        """Ask Claude to extract the original song title from a remix/edit title.

        Returns the resolved title string, or None if it can't resolve.
        """
        if not self._enabled:
            return None

        client = self._get_client()
        if not client:
            return None

        prompt = (
            f"Given this track title: \"{title}\""
            + (f" by {artist}" if artist else "")
            + "\n\nWhat is the original song title (without remix/edit/version suffixes)? "
            "Return ONLY the clean original title, nothing else. "
            "If this is already the original title, return it as-is."
        )

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            resolved = response.content[0].text.strip().strip('"').strip("'")
            if resolved and len(resolved) < 200:
                return resolved
        except Exception as e:
            logger.debug("AI resolve error: %s", e)

        return None
