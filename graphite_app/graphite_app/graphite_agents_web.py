import os
from PySide6.QtCore import QThread, Signal
import graphite_config as config
import api_provider

# --- Conditional Imports for Web Agent ---
try:
    from ddgs import DDGS
    DUCKDUCKGO_SEARCH_AVAILABLE = True
except ImportError:
    DUCKDUCKGO_SEARCH_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False


class WebSearchAgent:
    """
    An agent that performs a multi-step web search workflow: refine query, search,
    fetch content, validate content, and summarize.
    """
    def __init__(self):
        """Initializes the agent and checks for required dependencies."""
        self._check_dependencies()
        self.generate_query_prompt = """
You are a search query refinement assistant. Your task is to analyze a conversation history and a final user query to generate a self-contained, effective search engine query.

RULES:
1.  Read the conversation history to understand the context.
2.  Analyze the final user query.
3.  If the query is already self-contained and clear (e.g., "what is the capital of France"), return it exactly as is.
4.  If the query is contextual (e.g., "what about its population?"), use the history to create a specific, self-contained query (e.g., "population of France").
5.  Your output MUST be ONLY the refined search query string. Do not add any explanation, preamble, or quotation marks.
"""
        self.validation_prompt = """
You are a content validation bot. Your only purpose is to determine if a piece of retrieved web content is safe and relevant to a user's original query.

RULES:
1. First, check for safety. The content is UNSAFE if it contains any of the following:
    - Explicit adult content (pornography, graphic violence)
    - Hate speech, harassment, or discriminatory language
    - Dangerous or illegal instructions (e.g., self-harm, building weapons)
    - Deceptive content (scams, phishing, malware links)

2. Second, check for relevance. The content is IRRELEVANT if it does NOT directly help answer the user's query. It is also irrelevant if it is:
    - A login page, error page, or navigation menu with no useful content.
    - A product page with only specifications and no descriptive text.
    - A forum index page without actual discussion content.
    - Gibberish or non-prose text.

3. Your response MUST be a single word: `SAFE` or `UNSAFE`.
    - If the content is safe AND relevant, output `SAFE`.
    - If the content is unsafe OR irrelevant, output `UNSAFE`.
    - Do NOT provide any explanation or other text.
"""
        self.summarization_prompt = """
You are a web-grounded summarization assistant. You will be given a user's original query, the conversation history for context, and a block of text retrieved from one or more web pages. Your task is to synthesize this information into a single, comprehensive, and well-written answer to the user's query.

RULES:
1.  **Use the Conversation History:** The history provides crucial context. Your answer must be relevant to the ongoing conversation.
2.  **Directly Answer the Query:** Your primary goal is to answer the user's original question using the provided web content.
3.  **Synthesize, Don't List:** Combine information from different parts of the text to form a coherent response. Do not treat the text as separate sources to be summarized individually.
4.  **Be Concise:** Extract the most important information and present it clearly. Avoid unnecessary details or filler text.
5.  **Use Markdown:** Format your response for readability using headings, bullet points, and bold text where appropriate.
"""

    def _check_dependencies(self):
        """
        Raises an ImportError if any of the required web-related libraries are missing.
        """
        if not DUCKDUCKGO_SEARCH_AVAILABLE:
            raise ImportError("Web search requires `ddgs`. Please install it: pip install ddgs")
        if not REQUESTS_AVAILABLE:
            raise ImportError("Web fetching requires `requests`. Please install it: pip install requests")
        if not BEAUTIFULSOUP_AVAILABLE:
            raise ImportError("Web parsing requires `beautifulsoup4`. Please install it: pip install beautifulsoup4")

    def generate_search_query(self, query: str, history: list) -> str:
        """
        Refines a user's query based on conversation history to make it self-contained.

        Args:
            query (str): The user's latest query.
            history (list): The preceding conversation history.

        Returns:
            str: A refined, standalone search query.
        """
        if not history:
            return query  # No context, query is as good as it gets.

        history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
        user_prompt = f"""
--- Conversation History ---
{history_str}

--- Final User Query ---
{query}
"""
        try:
            # Use a fast model for this simple refinement task.
            response = api_provider.chat(
                task=config.TASK_TITLE,  # Re-using the title task model as it's meant to be fast
                messages=[
                    {'role': 'system', 'content': self.generate_query_prompt},
                    {'role': 'user', 'content': user_prompt}
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            print(f"Failed to generate search query, falling back to original. Error: {e}")
            return query  # Fallback to original query on error

    def search(self, query: str) -> list:
        """
        Performs a web search using DuckDuckGo Search.

        Args:
            query (str): The search query.

        Returns:
            list: A list of search result dictionaries.
        """
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        return results

    def fetch_content(self, url: str) -> (str | None, str | None):
        """
        Fetches and cleans the text content from a given URL, applying streaming
        and chunking to prevent memory exhaustion from massive files.

        Args:
            url (str): The URL to fetch.

        Returns:
            tuple[str or None, str or None]: A tuple containing the cleaned text content
                                             and an error message if an error occurred.
        """
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            # Enforce streaming to prevent downloading massive payloads directly into memory
            response = requests.get(url, headers=headers, timeout=10, stream=True)
            response.raise_for_status()

            # Enforce a hard byte-read limit (2MB)
            MAX_BYTES = 2 * 1024 * 1024 
            content_bytes = b""
            for chunk in response.iter_content(chunk_size=8192):
                content_bytes += chunk
                if len(content_bytes) > MAX_BYTES:
                    break

            soup = BeautifulSoup(content_bytes, 'html.parser')
            # Remove script, style, and common boilerplate elements.
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.extract()
            
            # Extract and clean up the text.
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            return text, None
        except requests.RequestException as e:
            return None, f"Failed to fetch URL {url}: {e}"
        except Exception as e:
            return None, f"Failed to parse content from {url}: {e}"

    def validate_content(self, query: str, content: str) -> bool:
        """
        Uses an LLM to validate if fetched content is safe and relevant to the original query.

        Args:
            query (str): The original search query.
            content (str): The fetched web page content.

        Returns:
            bool: True if the content is deemed 'SAFE', False otherwise.
        """
        # Truncate content to avoid excessive token usage for a simple validation step.
        truncated_content = content[:4000]
        
        user_prompt = f"""
Original User Query: "{query}"

--- Retrieved Web Content ---
{truncated_content}
--- End of Content ---

Based on the rules, is this content safe and relevant? Respond with only `SAFE` or `UNSAFE`.
"""
        try:
            # This validation step now uses the api_provider to be mode-agnostic.
            messages = [
                {'role': 'system', 'content': self.validation_prompt},
                {'role': 'user', 'content': user_prompt}
            ]
            
            response = api_provider.chat(task=config.TASK_WEB_VALIDATE, messages=messages)
            decision = response['message']['content'].strip().upper()
            
            return "SAFE" in decision
        except Exception as e:
            print(f"Content validation failed: {e}")
            # Re-raise with a more user-friendly message for the UI.
            raise RuntimeError(f"Content validation step failed: {e}")

    def summarize_content(self, query: str, validated_content: str, history: list) -> str:
        """
        Synthesizes the validated web content into a final answer for the user.

        Args:
            query (str): The user's original query.
            validated_content (str): The combined text from all validated sources.
            history (list): The preceding conversation history for context.

        Returns:
            str: A formatted summary answering the user's query.
        """
        history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
        user_prompt = f"""
--- Conversation History ---
{history_str}

--- Original User Query for this step ---
"{query}"

--- Validated Web Content ---
{validated_content}
--- End of Content ---

Please provide a comprehensive answer to the original query based on the content provided and the conversation history for context.
"""
        try:
            response = api_provider.chat(
                task=config.TASK_WEB_SUMMARIZE,
                messages=[
                    {'role': 'system', 'content': self.summarization_prompt},
                    {'role': 'user', 'content': user_prompt}
                ]
            )
            return response['message']['content']
        except Exception as e:
            raise RuntimeError(f"Failed to summarize web content: {e}")


class WebWorkerThread(QThread):
    """Orchestrates the WebSearchAgent's workflow in a background thread."""
    update_status = Signal(str) # Emits status updates for the UI.
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, query: str, history: list):
        super().__init__()
        self.query = query
        self.history = history
        self.agent = WebSearchAgent()
        self._is_running = True

    def run(self):
        """Executes the full web search workflow step-by-step."""
        try:
            if not self._is_running: return

            # 1. Generate a context-aware search query.
            self.update_status.emit("Refining search query...")
            effective_query = self.agent.generate_search_query(self.query, self.history)

            # 2. Perform the web search.
            if not self._is_running: return
            self.update_status.emit(f"Searching for: \"{effective_query}\"...")
            results = self.agent.search(effective_query)
            if not results:
                raise ValueError("No search results found for your query.")

            # 3. Fetch, clean, and validate content from the top search results.
            if not self._is_running: return
            validated_texts = []
            source_urls = []
            for i, result in enumerate(results[:3]): # Process top 3 results
                if not self._is_running: return
                url = result.get('href')
                if not url: continue
                
                self.update_status.emit(f"Fetching content from result {i+1}...")
                content, error = self.agent.fetch_content(url)
                if error or not content:
                    print(f"Skipping {url}: {error}")
                    continue

                if not self._is_running: return
                self.update_status.emit(f"Validating result {i+1}...")
                # Use the refined `effective_query` for more accurate validation.
                if self.agent.validate_content(effective_query, content):
                    validated_texts.append(content)
                    source_urls.append(url)
            
            if not self._is_running: return
            if not validated_texts:
                raise ValueError("No relevant and safe content could be retrieved from the web.")

            # 4. Synthesize the validated content into a final summary.
            self.update_status.emit("Synthesizing information...")
            combined_content = "\n\n---\n\n".join(validated_texts)
            # Pass the original query and history to the summarizer for full context.
            summary = self.agent.summarize_content(self.query, combined_content, self.history)

            if self._is_running:
                self.finished.emit({
                    "summary": summary,
                    "sources": source_urls,
                    "query": self.query # Keep original query for the node's display
                })
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False
            
    def stop(self):
        """Stops the thread safely."""
        self._is_running = False