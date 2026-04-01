import importlib
import os
from pathlib import Path

# --- Conditional Imports for Optional Dependencies ---
# These libraries are not core requirements for the application to run, but they
# are necessary for handling specific file types (.pdf, .docx). By using a
# try-except block, the application can start even if these libraries aren't
# installed and gracefully inform the user if they attempt to use a feature
# that requires a missing dependency.

# Attempt to import a PDF reader implementation.
try:
    pdf_reader_lib = importlib.import_module("pypdf")
    PDF_AVAILABLE = True
    PDF_IMPORT_NAME = "pypdf"
except ImportError:
    try:
        pdf_reader_lib = importlib.import_module("PyPDF2")
        PDF_AVAILABLE = True
        PDF_IMPORT_NAME = "PyPDF2"
    except ImportError:
        pdf_reader_lib = None
        PDF_AVAILABLE = False
        PDF_IMPORT_NAME = None

# Attempt to import python-docx for reading .docx files.
try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

class FileHandler:
    """
    Handles reading and extracting text content from various file types.

    This class provides a unified interface to read plain text, PDF, and DOCX files,
    gracefully handling missing optional dependencies for PDF and DOCX processing.
    It acts as a dispatcher, selecting the appropriate reading method based on the
    file's extension. This centralizes file reading logic and makes it easy to
    extend with support for new file formats in the future.
    """

    # A wider set of common text and source-code formats that can be read
    # directly and injected into the prompt as attachment context.
    PLAIN_TEXT_EXTENSIONS = {
        '.bat', '.c', '.cc', '.cfg', '.conf', '.cpp', '.cs', '.css', '.csv',
        '.env', '.go', '.h', '.hpp', '.html', '.ini', '.java', '.js', '.json',
        '.jsx', '.kt', '.kts', '.log', '.lua', '.md', '.mdx', '.php', '.ps1',
        '.py', '.rb', '.rs', '.rst', '.sh', '.sql', '.svg', '.swift', '.tex',
        '.toml', '.ts', '.tsx', '.txt', '.xml', '.yaml', '.yml',
    }
    SUPPORTED_FILENAMES = {
        '.editorconfig', '.env', '.gitignore', 'Dockerfile', 'Gemfile',
        'Makefile', 'Procfile', 'README', 'README.md', 'requirements.txt',
    }
    PDF_INSTALL_MESSAGE = (
        "PDF support is not installed. Please run: pip install pypdf"
    )

    def __init__(self):
        """
        Initializes the FileHandler.

        This method dynamically expands the set of supported file extensions based on
        which optional libraries (like pypdf, python-docx) were successfully imported
        when the application started.
        """
        # Add .pdf support if the pypdf library was successfully imported.
        self.SUPPORTED_EXTENSIONS = set(self.PLAIN_TEXT_EXTENSIONS)
        if PDF_AVAILABLE:
            self.SUPPORTED_EXTENSIONS.add('.pdf')
        # Add .docx support if the python-docx library was successfully imported.
        if DOCX_AVAILABLE:
            self.SUPPORTED_EXTENSIONS.add('.docx')

    def can_read_file(self, file_path: str) -> bool:
        """
        Returns True when a file can be safely treated as a readable attachment.
        """
        path = Path(file_path)
        if not path.is_file():
            return False

        ext = path.suffix.lower()
        if ext in self.SUPPORTED_EXTENSIONS:
            return True

        if path.name in self.SUPPORTED_FILENAMES:
            return True

        return self._looks_like_text_file(path)

    def read_file(self, file_path: str) -> tuple[str | None, str | None]:
        """
        Reads a file and returns its content as a string.

        This is the main public method of the class. It validates the file path,
        determines the file type from its extension, and calls the appropriate
        private reader method. It returns a tuple where the first element is the
        file content and the second is an error message if one occurred.

        Args:
            file_path (str): The absolute path to the file to be read.

        Returns:
            tuple[str | None, str | None]: A tuple containing (content, error_message).
                                           On success, content is a string and error_message is None.
                                           On failure, content is None and error_message is a string.
        """
        path = Path(file_path)
        # First, validate that the provided path actually points to a file.
        if not path.is_file():
            return None, f"File not found: {file_path}"

        # Get the file extension in lowercase to ensure case-insensitive matching.
        ext = path.suffix.lower()

        try:
            # Dispatch to the correct reader method based on the file extension.
            if ext == '.pdf':
                # Check if the required library is available before attempting to read.
                if not PDF_AVAILABLE:
                    return None, self.PDF_INSTALL_MESSAGE
                return self._read_pdf(path), None
            elif ext == '.docx':
                # Check if the required library is available before attempting to read.
                if not DOCX_AVAILABLE:
                    return None, "Word document support is not installed. Please run: pip install python-docx"
                return self._read_docx(path), None
            elif (
                ext in self.PLAIN_TEXT_EXTENSIONS
                or path.name in self.SUPPORTED_FILENAMES
                or self._looks_like_text_file(path)
            ):
                return self._read_text(path), None
            else:
                # If the extension is not in our supported list, return an error.
                return None, f"Unsupported file type: {ext}"
        except Exception as e:
            # Catch any unexpected errors during file processing.
            return None, f"Error reading file '{path.name}': {str(e)}"

    def _read_text(self, path: Path) -> str:
        """
        Reads plain text files using standard file I/O.

        Args:
            path (Path): The Path object representing the file to read.

        Returns:
            str: The content of the file as a string.
        """
        raw_bytes = path.read_bytes()
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'latin-1'):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='ignore')

    def _looks_like_text_file(self, path: Path) -> bool:
        """
        Heuristically detect text-like files so uncommon source/config/log files
        can still be attached without maintaining an exhaustive extension list.
        """
        try:
            sample = path.read_bytes()[:4096]
        except OSError:
            return False

        if not sample:
            return True

        if b'\x00' in sample:
            return False

        text_bytes = bytes(range(32, 127)) + b'\n\r\t\f\b'
        non_text_count = sum(byte not in text_bytes for byte in sample)
        return (non_text_count / len(sample)) < 0.30

    def _read_pdf(self, path: Path) -> str:
        """
        Reads and extracts text from a PDF file using the pypdf library.

        Args:
            path (Path): The Path object representing the PDF file.

        Returns:
            str: The extracted text content, with pages joined by newlines.
        """
        content = []
        extracted_characters = 0
        # Open the file in binary read mode ('rb') as required by common PDF readers.
        with open(path, 'rb') as f:
            reader = pdf_reader_lib.PdfReader(f)
            # Try a layout-preserving extraction first when supported, then fall back.
            for page_number, page in enumerate(reader.pages, start=1):
                page_text = self._extract_pdf_page_text(page)
                if page_text:
                    normalized_text = page_text.strip()
                    content.append(normalized_text)
                    extracted_characters += len(normalized_text)
                else:
                    content.append(f"[Page {page_number}: no extractable text found]")

        combined = "\n\n".join(part for part in content if part)
        if extracted_characters == 0:
            raise ValueError(
                "No readable text could be extracted from this PDF. "
                "It may be image-based, scanned, encrypted, or use an unsupported text encoding."
            )
        return combined

    def _extract_pdf_page_text(self, page) -> str:
        """
        Extracts text from a PDF page while remaining compatible with multiple
        PDF reader implementations and versions.
        """
        extraction_attempts = (
            {"extraction_mode": "layout"},
            {},
        )
        for kwargs in extraction_attempts:
            try:
                text = page.extract_text(**kwargs)
            except TypeError:
                continue
            except Exception:
                text = None
            if text and text.strip():
                return text
        return ""

    def _read_docx(self, path: Path) -> str:
        """
        Reads and extracts text from a .docx file using the python-docx library.

        Args:
            path (Path): The Path object representing the DOCX file.

        Returns:
            str: The extracted text content, with paragraphs joined by newlines.
        """
        doc = docx.Document(path)
        # Iterate through each paragraph in the document and extract its text.
        content = [para.text for para in doc.paragraphs]
        # Join the text from all paragraphs into a single string.
        return "\n".join(content)
