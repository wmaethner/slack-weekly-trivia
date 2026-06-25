import requests


class TriviaApi:
    """Adapter for the-trivia-api.com. Swap this class to change API provider."""

    BASE_URL = "https://the-trivia-api.com/v2"

    def fetch_question(self):
        """Fetch a random family-safe text_choice trivia question."""
        response = requests.get(
            f"{self.BASE_URL}/questions",
            params={
                "limit": 1,
                "types": "text_choice",
                "contentFilter": "family",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError("No questions returned from API")
        return data[0]
