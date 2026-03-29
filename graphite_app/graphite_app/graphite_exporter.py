import os
from PySide6.QtWidgets import QMessageBox

# --- Conditional Imports for Optional Dependencies ---

# Attempt to import reportlab for PDF generation.
try:
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.colors import black, white
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# Attempt to import python-docx for DOCX generation.
try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

# Attempt to import markdown for HTML generation.
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

class Exporter:
    """
    A utility class that handles exporting string content to various file formats.
    
    This class centralizes the logic for writing to different file types, managing
    optional dependencies for formats like PDF and DOCX.
    """
    def __init__(self):
        """
        Initializes the Exporter and defines user-friendly error messages for
        missing optional libraries.
        """
        self.importer_errors = {
            'pdf': "PDF export requires the 'reportlab' library. Please install it by running: pip install reportlab",
            'docx': "DOCX export requires the 'python-docx' library. Please install it by running: pip install python-docx"
        }

    def export_to_txt(self, content, file_path):
        """
        Exports content to a plain text (.txt) file.

        Args:
            content (str): The string content to write to the file.
            file_path (str): The full path of the file to save.

        Returns:
            tuple[bool, str | None]: A tuple containing a success flag and an
                                     error message string if an error occurred.
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, str(e)

    def export_to_py(self, content, file_path):
        """
        Exports content to a Python (.py) file.

        Args:
            content (str): The string content (source code) to write to the file.
            file_path (str): The full path of the file to save.

        Returns:
            tuple[bool, str | None]: A tuple containing a success flag and an
                                     error message string if an error occurred.
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, str(e)
            
    def export_to_md(self, content, file_path):
        """
        Exports content to a Markdown (.md) file.

        Args:
            content (str): The Markdown string to write to the file.
            file_path (str): The full path of the file to save.

        Returns:
            tuple[bool, str | None]: A tuple containing a success flag and an
                                     error message string if an error occurred.
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, str(e)

    def export_to_html(self, content, file_path, title="Graphite Export"):
        """
        Exports Markdown content to a styled HTML file.

        Args:
            content (str): The Markdown content to convert and save.
            file_path (str): The full path of the file to save.
            title (str, optional): The title for the HTML document.

        Returns:
            tuple[bool, str | None]: A tuple containing a success flag and an
                                     error message string if an error occurred.
        
        Raises:
            ImportError: If the 'markdown' library is not installed.
        """
        if not MARKDOWN_AVAILABLE:
            raise ImportError("HTML export requires the 'markdown' library. Please install it with: pip install markdown")
        try:
            # Convert Markdown to HTML body.
            html_body = markdown.markdown(content, extensions=['fenced_code', 'tables'])
            # Wrap the body in a full HTML document with basic styling.
            html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: sans-serif; line-height: 1.6; padding: 2em; background-color: #f4f4f4; color: #333; }}
        .container {{ max-width: 800px; margin: auto; background: white; padding: 2em; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
        pre {{ background-color: #2d2d2d; color: #f8f8f2; padding: 1em; border-radius: 5px; overflow-x: auto; }}
        code {{ font-family: monospace; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="container">
        {html_body}
    </div>
</body>
</html>
            """
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            return True, None
        except Exception as e:
            return False, str(e)

    def export_to_docx(self, content, file_path):
        """
        Exports content to a Word Document (.docx) file.

        Args:
            content (str): The string content to write to the document.
            file_path (str): The full path of the file to save.

        Returns:
            tuple[bool, str | None]: A tuple containing a success flag and an
                                     error message string if an error occurred.
        
        Raises:
            ImportError: If the 'python-docx' library is not installed.
        """
        if not DOCX_AVAILABLE:
            raise ImportError(self.importer_errors['docx'])
        try:
            document = docx.Document()
            document.add_paragraph(content)
            document.save(file_path)
            return True, None
        except Exception as e:
            return False, str(e)

    def export_to_pdf(self, content, file_path, is_code=False):
        """
        Exports content to a PDF document.

        Args:
            content (str): The string content to write to the document.
            file_path (str): The full path of the file to save.
            is_code (bool, optional): If True, formats the content using a
                                      monospace font. Defaults to False.

        Returns:
            tuple[bool, str | None]: A tuple containing a success flag and an
                                     error message string if an error occurred.
        
        Raises:
            ImportError: If the 'reportlab' library is not installed.
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError(self.importer_errors['pdf'])
            
        try:
            doc = SimpleDocTemplate(file_path, pagesize=(8.5 * inch, 11 * inch))
            styles = getSampleStyleSheet()
            
            # Choose the appropriate style based on content type.
            if is_code:
                style = styles['Code']
                style.fontName = 'Courier'
                style.fontSize = 9
                style.leading = 12
            else:
                style = styles['BodyText']
                style.alignment = TA_LEFT
                style.fontSize = 10
                style.leading = 14

            # Build the story (list of flowables) for the PDF.
            story = []
            paragraphs = content.split('\n')
            for para in paragraphs:
                if not para.strip():
                    # Add a small spacer for empty lines.
                    story.append(Spacer(1, 0.1 * inch))
                else:
                    # ReportLab requires non-breaking spaces to preserve whitespace.
                    p = Paragraph(para.replace(' ', '&nbsp;'), style)
                    story.append(p)
            
            doc.build(story)
            return True, None
        except Exception as e:
            return False, str(e)