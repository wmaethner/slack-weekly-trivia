import requests


class TriviaApi:
    """Adapter for the-trivia-api.com. Swap this class to change API provider."""

    BASE_URL = "https://the-trivia-api.com/v2"

    def fetch_question(self):
        """Fetch a single random family-safe text_choice trivia question."""
        questions = self.fetch_questions(limit=1)
        return questions[0]

    def fetch_questions(self, limit=20):
        """Fetch a batch of random family-safe text_choice trivia questions."""
        response = requests.get(
            f"{self.BASE_URL}/questions",
            params={
                "limit": limit,
                "types": "text_choice",
                "contentFilter": "family",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError("No questions returned from API")
        return data
