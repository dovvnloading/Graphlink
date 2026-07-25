import json
import ollama
from PySide6.QtCore import QThread, Signal
import api_provider
from graphlink_chart_agent import ChartDataAgent

# R6.2: ChartDataAgent itself moved to graphlink_chart_agent.py - this
# module's unconditional `from PySide6.QtCore import QThread, Signal` (needed
# only by ChartWorkerThread/ImageGenerationWorkerThread/ModelPullWorkerThread
# below) meant importing anything from it, including the Qt-free
# ChartDataAgent, pulled PySide6 into the process. Re-exported here unchanged
# so ChartWorkerThread (which constructs one internally) and the legacy Qt
# call site (graphlink_window_actions.py, which imports ChartWorkerThread)
# keep working unmodified - only the class's true home moved. Same split R5.2
# already did for ArtifactAgent/graphlink_artifact_agent.py.


class ChartWorkerThread(QThread):
    """QThread worker for the ChartDataAgent."""
    finished = Signal(str, str)
    error = Signal(str)

    def __init__(self, text, chart_type):
        super().__init__()
        self.agent = ChartDataAgent()
        self.text = text
        self.chart_type = chart_type

    def run(self):
        """Executes the agent and validates the response before emitting."""
        try:
            data = self.agent.get_response(self.text, self.chart_type)
            # Validate that the response is valid JSON and does not contain an error key.
            parsed = json.loads(data)
            if 'error' in parsed:
                raise ValueError(parsed['error'])
            self.finished.emit(data, self.chart_type)
        except Exception as e:
            self.error.emit(str(e))


class ImageGenerationAgent:
    """An agent that generates an image from a text prompt."""
    def __init__(self):
        pass

    def get_response(self, prompt: str):
        """
        Calls the api_provider to generate an image.

        Args:
            prompt (str): The text prompt for the image generation.

        Returns:
            bytes: The raw byte data of the generated image.

        Raises:
            Exception: Propagates exceptions from the API provider.
        """
        try:
            image_bytes = api_provider.generate_image(prompt)
            return image_bytes
        except Exception as e:
            # Propagate the exception to be handled by the worker thread
            raise e


class ImageGenerationWorkerThread(QThread):
    """QThread worker for the ImageGenerationAgent."""
    finished = Signal(bytes, str)  # image_bytes, original_prompt
    error = Signal(str)

    def __init__(self, agent, prompt):
        super().__init__()
        self.agent = agent
        self.prompt = prompt
        self._is_running = True

    def run(self):
        """Executes the agent and emits the resulting image bytes."""
        try:
            if not self._is_running:
                return
            image_bytes = self.agent.get_response(self.prompt)
            if self._is_running:
                self.finished.emit(image_bytes, self.prompt)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False

    def stop(self):
        """Stops the thread safely."""
        self._is_running = False


class ModelPullWorkerThread(QThread):
    """
    A QThread worker for downloading Ollama models in the background.
    This is used in the settings dialog to prevent the UI from freezing during a pull.
    """
    status_update = Signal(str) # Emits progress messages.
    finished = Signal(str, str) # Emits success message and model name.
    error = Signal(str)         # Emits error message.

    def __init__(self, model_name):
        super().__init__()
        self.model_name = model_name

    def run(self):
        """Executes the `ollama.pull` command."""
        try:
            self.status_update.emit(f"Ensuring model '{self.model_name}' is available...")

            # This is a blocking call, hence the need for a thread.
            ollama.pull(self.model_name)

            # A (re-)pull can change what this model reports for capabilities (e.g. a
            # newer build gaining audio support) - drop any cached answer from before
            # this pull so the next capability check re-fetches it.
            api_provider.invalidate_ollama_capability_cache(self.model_name)

            self.finished.emit(f"Model '{self.model_name}' is ready to use.", self.model_name)

        except Exception as e:
            # Provide user-friendly error messages for common issues.
            error_message = str(e)
            if "not found" in error_message.lower():
                self.error.emit(f"Model '{self.model_name}' not found on the Ollama hub. Please check the name for typos.")
            elif "connection refused" in error_message.lower():
                self.error.emit("Connection to Ollama server failed. Is Ollama running?")
            else:
                self.error.emit(f"An unexpected error occurred: {error_message}")
