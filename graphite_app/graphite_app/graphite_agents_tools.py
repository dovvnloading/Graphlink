import json
import re
import ollama
from PySide6.QtCore import QThread, Signal
import graphite_config as config
import api_provider


class ChartDataAgent:
    """
    An agent that extracts structured data from natural language text to generate JSON
    payloads suitable for creating various types of charts.
    """
    # A dictionary of system prompts, one for each supported chart type.
    # Each prompt strictly defines the required JSON output format.
    CHART_PROMPTS = {
        'bar': """You are a data extraction agent specializing in creating JSON for BAR charts.
Your task is to analyze the user's text and extract data to fit the following JSON structure precisely.

STRUCTURE:
{
    "type": "bar",
    "title": "<A concise title for the chart>",
    "labels": ["<label_for_value_1>", "<label_for_value_2>", ...],
    "values": [<numeric_value_1>, <numeric_value_2>, ...],
    "xAxis": "<Label for the X-axis>",
    "yAxis": "<Label for the Y-axis>"
}

EXAMPLE:
User Text: "The report shows our Q3 sales figures. We sold 150 units of Product A, 220 of Product B, and 95 of Product C. This is an overview of sales per product."
Your Output:
{
    "type": "bar",
    "title": "Q3 Sales Figures",
    "labels": ["Product A", "Product B", "Product C"],
    "values": [150, 220, 95],
    "xAxis": "Product",
    "yAxis": "Units Sold"
}

RULES:
1. ONLY output the raw JSON object. Do not include `json`, markdown backticks, or any explanatory text.
2. The `labels` and `values` arrays must have the same number of elements.
3. If you cannot find appropriate data in the text, return this exact error object: {"error": "Could not find sufficient data to generate a bar chart."}""",

        'line': """You are a data extraction agent specializing in creating JSON for LINE charts.
Your task is to analyze the user's text and extract data to fit the following JSON structure precisely.

STRUCTURE:
{
    "type": "line",
    "title": "<A concise title for the chart>",
    "labels": ["<point_1_label>", "<point_2_label>", ...],
    "values": [<numeric_value_1>, <numeric_value_2>, ...],
    "xAxis": "<Label for the X-axis, often time or sequence>",
    "yAxis": "<Label for the Y-axis>"
}

EXAMPLE:
User Text: "Our website traffic over the last week was: Monday 1200 visitors, Tuesday 1350, Wednesday 1600, Thursday 1550, and Friday 1800. Let's visualize weekly visitor trends."
Your Output:
{
    "type": "line",
    "title": "Weekly Visitor Trends",
    "labels": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "values": [1200, 1350, 1600, 1550, 1800],
    "xAxis": "Day of the Week",
    "yAxis": "Number of Visitors"
}

RULES:
1. ONLY output the raw JSON object. Do not include `json`, markdown backticks, or any explanatory text.
2. The `labels` and `values` arrays must have the same number of elements.
3. If you cannot find appropriate data in the text, return this exact error object: {"error": "Could not find sufficient data to generate a line chart."}""",

        'pie': """You are a data extraction agent specializing in creating JSON for PIE charts.
Your task is to analyze the user's text and extract data representing parts of a whole to fit the following JSON structure precisely.

STRUCTURE:
{
    "type": "pie",
    "title": "<A concise title for the chart>",
    "labels": ["<category_1>", "<category_2>", ...],
    "values": [<numeric_value_1>, <numeric_value_2>, ...]
}

EXAMPLE:
User Text: "Our team's budget allocation is as follows: 50% for salaries, 30% for marketing, 15% for software, and 5% for miscellaneous expenses."
Your Output:
{
    "type": "pie",
    "title": "Team Budget Allocation",
    "labels": ["Salaries", "Marketing", "Software", "Miscellaneous"],
    "values": [50, 30, 15, 5]
}

RULES:
1. ONLY output the raw JSON object. Do not include `json`, markdown backticks, or any explanatory text.
2. The `labels` and `values` arrays must have the same number of elements.
3. If you cannot find appropriate data in the text, return this exact error object: {"error": "Could not find sufficient data for a pie chart."}""",

        'histogram': """You are a data extraction agent specializing in creating JSON for HISTOGRAMS.
Your task is to analyze the user's text and extract a dataset of raw numerical values to fit the following JSON structure precisely.

STRUCTURE:
{
    "type": "histogram",
    "title": "<A concise title for the chart>",
    "values": [<raw_numeric_value_1>, <raw_numeric_value_2>, ...],
    "bins": 10,
    "xAxis": "<Label for the values being measured>",
    "yAxis": "Frequency"
}

EXAMPLE:
User Text: "Here are the scores from the recent exam: 88, 92, 75, 81, 95, 88, 79, 83, 85, 91, 77, 89, 88. Let's see the score distribution."
Your Output:
{
    "type": "histogram",
    "title": "Exam Score Distribution",
    "values": [88, 92, 75, 81, 95, 88, 79, 83, 85, 91, 77, 89, 88],
    "bins": 10,
    "xAxis": "Exam Scores",
    "yAxis": "Frequency"
}

RULES:
1. ONLY output the raw JSON object. Do not include `json`, markdown backticks, or any explanatory text.
2. The `values` key must contain an array of raw numbers, not aggregated data.
3. If you cannot find a dataset of raw numbers, return this exact error object: {"error": "Could not find a valid dataset to generate a histogram."}""",

        'sankey': """You are a data extraction agent specializing in creating JSON for SANKEY diagrams.
Your task is to analyze text describing a flow or distribution between different stages or categories and extract this data to fit the following JSON structure precisely.

STRUCTURE:
{
    "type": "sankey",
    "title": "<A concise title for the flow diagram>",
    "flows": [
        {"source": "<source_category>", "target": "<target_category>", "value": <numeric_value>},
        ...
    ]
}

EXAMPLE:
User Text: "In our energy plant, we start with 1000 units of raw coal. 200 units are lost during transport. Of the remaining 800, 500 units are converted to electricity and 300 are lost as heat. The 500 electricity units are then sent to the grid."
Your Output:
{
    "type": "sankey",
    "title": "Energy Plant Flow",
    "flows": [
        {"source": "Raw Coal", "target": "Transport Loss", "value": 200},
        {"source": "Raw Coal", "target": "Processing", "value": 800},
        {"source": "Processing", "target": "Electricity", "value": 500},
        {"source": "Processing", "target": "Heat Loss", "value": 300},
        {"source": "Electricity", "target": "Grid", "value": 500}
    ]
}

RULES:
1. ONLY output the raw JSON object. Do not include `json`, markdown backticks, or any explanatory text.
2. Each item in the `flows` array must have a `source`, `target`, and `value`.
3. If you cannot identify a flow between distinct categories, return this exact error object: {"error": "Could not extract valid flow data for a Sankey diagram."}"""
    }

    CHART_SCHEMA_TEMPLATES = {
        'bar': '{"type":"bar","title":"<title>","labels":["<label_1>","<label_2>"],"values":[1,2],"xAxis":"<x-axis>","yAxis":"<y-axis>"}',
        'line': '{"type":"line","title":"<title>","labels":["<point_1>","<point_2>"],"values":[1,2],"xAxis":"<x-axis>","yAxis":"<y-axis>"}',
        'pie': '{"type":"pie","title":"<title>","labels":["<category_1>","<category_2>"],"values":[50,30]}',
        'histogram': '{"type":"histogram","title":"<title>","values":[1,2,3,4],"bins":10,"xAxis":"<x-axis>","yAxis":"Frequency"}',
        'sankey': '{"type":"sankey","title":"<title>","flows":[{"source":"<source>","target":"<target>","value":10}]}',
    }

    CHART_OUTPUT_HARD_RULES = """
STRICT OUTPUT RULES:
- Return exactly one top-level raw JSON object and nothing else.
- Do not wrap the JSON in markdown, prose, or explanations.
- Do not nest the final payload under keys like chart, data, payload, result, output, response, or content.
- For bar, line, and pie charts, the final object must include top-level `labels` and `values` arrays.
- For histogram charts, the final object must include top-level `values`, `bins`, `xAxis`, and `yAxis`.
- For sankey charts, the final object must include a top-level `flows` array.
- Do not use alternate keys like items, segments, categories, amounts, records, series, or links in place of the required final schema.
- If the source text does not support the requested chart, return only an `error` object.
"""

    def clean_response(self, text):
        """
        Cleans the raw string response from the LLM to isolate the JSON object.
        Models often wrap JSON in markdown backticks or add explanatory text.

        Args:
            text (str): The raw response string.

        Returns:
            str: The cleaned string, hopefully containing only a valid JSON object.
        """
        text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()

        # 1. Try to extract from markdown code blocks first using regex
        block_match = re.search(r'```(?:json)?\s*([\[{][\s\S]*?[\]}])\s*```', text, re.IGNORECASE)
        if block_match:
            return block_match.group(1).strip()
            
        # 2. Fallback: regex to find the first { and last } robustly
        fallback_match = re.search(r'([\[{][\s\S]*[\]}])', text)
        if fallback_match:
            return fallback_match.group(1).strip()
             
        return text.strip()

    def _build_chart_system_prompt(self, chart_type):
        return (
            "You are Graphite's chart payload extractor.\n"
            f"Your job is to read source text and produce a valid {chart_type} chart payload.\n"
            "Return exactly one raw JSON object and nothing else.\n"
            "Do not include markdown fences, explanations, commentary, or wrapper keys.\n"
            "Do not return a nested object like chart/data/result/output/content.\n"
            f"{self.CHART_OUTPUT_HARD_RULES}\n"
            f"EXACT FINAL SCHEMA:\n{self.CHART_SCHEMA_TEMPLATES[chart_type]}\n\n"
            "If the source text does not contain enough information, return only:\n"
            '{"error":"<brief reason>"}'
        )

    def _build_chart_user_prompt(self, text, chart_type):
        return (
            f"Analyze the source text and extract a {chart_type} chart payload.\n"
            f"Return only one JSON object that matches the exact final schema.\n\n"
            f"--- SOURCE TEXT ---\n{text}"
        )

    def _build_chart_repair_prompt(self, chart_type):
        return (
            "You repair malformed chart JSON into a strict Graphite chart payload.\n"
            "Return only one raw JSON object matching the exact final schema.\n"
            "Do not wrap the payload in other keys.\n"
            "If the malformed payload is unusable, regenerate the chart payload from the source text.\n"
            "If the source text still does not support the chart, return only an error object.\n\n"
            f"{self.CHART_OUTPUT_HARD_RULES}\n"
            f"EXACT FINAL SCHEMA:\n{self.CHART_SCHEMA_TEMPLATES[chart_type]}"
        )

    def _walk_nested_values(self, obj, max_depth=5):
        if max_depth < 0:
            return
        yield obj
        if isinstance(obj, dict):
            for value in obj.values():
                yield from self._walk_nested_values(value, max_depth - 1)
        elif isinstance(obj, list):
            for value in obj:
                yield from self._walk_nested_values(value, max_depth - 1)

    def _coerce_numeric_value(self, value):
        try:
            return float(str(value).replace(',', '').replace('%', '').strip())
        except (TypeError, ValueError):
            return None

    def _clean_chart_label(self, label):
        cleaned = re.sub(r'\s+', ' ', str(label)).strip(" -:;,.")
        cleaned = re.sub(r'^(for|of|to|in|on)\s+', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(units?|users?|visitors?|people|items|tasks|hours?|days?|weeks?|months?|percent|percentage|%)\b\s*$', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip(" -:;,.")

    def _extract_pairs_from_text(self, text):
        clauses = [chunk.strip() for chunk in re.split(r'[\n;,]+', text) if chunk.strip()]
        pairs = []

        label_before_patterns = [
            re.compile(r'^(?P<label>[A-Za-z][A-Za-z0-9/&()\- ]{1,60}?)\s*(?:[:=\-]|(?:is|was|were|at))?\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*%?(?:\b.*)?$', re.IGNORECASE),
            re.compile(r'^(?P<label>[A-Za-z][A-Za-z0-9/&()\- ]{1,60}?)\s+(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*%?(?:\b.*)?$', re.IGNORECASE),
        ]
        value_before_patterns = [
            re.compile(r'^(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*%?\s*(?:for|of|to|in|on)?\s*(?P<label>[A-Za-z][A-Za-z0-9/&()\- ]{1,60})$', re.IGNORECASE),
        ]

        for clause in clauses:
            match = None
            for pattern in label_before_patterns:
                match = pattern.search(clause)
                if match:
                    label = self._clean_chart_label(match.group('label'))
                    value = self._coerce_numeric_value(match.group('value'))
                    if label and value is not None:
                        pairs.append((label, value))
                    break
            if match:
                continue

            for pattern in value_before_patterns:
                match = pattern.search(clause)
                if match:
                    label = self._clean_chart_label(match.group('label'))
                    value = self._coerce_numeric_value(match.group('value'))
                    if label and value is not None:
                        pairs.append((label, value))
                    break

        deduped = []
        seen = set()
        for label, value in pairs:
            key = (label.lower(), value)
            if key in seen:
                continue
            seen.add(key)
            deduped.append((label, value))
        return deduped

    def _extract_numbers_from_text(self, text):
        values = []
        for match in re.finditer(r'-?\d[\d,]*(?:\.\d+)?', text):
            number = self._coerce_numeric_value(match.group(0))
            if number is not None:
                values.append(number)
        return values

    def heuristic_chart_data(self, text, chart_type):
        source_text = str(text or '').strip()
        if not source_text:
            return None

        title = source_text.splitlines()[0].strip()[:80] or f"{chart_type.title()} Chart"
        pairs = self._extract_pairs_from_text(source_text)

        if chart_type in ['bar', 'line', 'pie'] and len(pairs) >= 2:
            labels = [label for label, _ in pairs]
            values = [value for _, value in pairs]
            payload = {
                'type': chart_type,
                'title': title,
                'labels': labels,
                'values': values,
            }
            if chart_type == 'bar':
                payload['xAxis'] = 'Category'
                payload['yAxis'] = 'Value'
            elif chart_type == 'line':
                payload['xAxis'] = 'Sequence'
                payload['yAxis'] = 'Value'
            return payload

        if chart_type == 'histogram':
            values = self._extract_numbers_from_text(source_text)
            if len(values) >= 4:
                return {
                    'type': 'histogram',
                    'title': title,
                    'values': values,
                    'bins': min(12, max(4, len(values) // 2)),
                    'xAxis': 'Value',
                    'yAxis': 'Frequency',
                }

        if chart_type == 'sankey':
            flow_pattern = re.compile(
                r'(?P<source>[A-Za-z][A-Za-z0-9/&()\- ]{1,40})\s*(?:->|to)\s*(?P<target>[A-Za-z][A-Za-z0-9/&()\- ]{1,40})\s*[:=-]?\s*(?P<value>\d[\d,]*(?:\.\d+)?)',
                re.IGNORECASE,
            )
            flows = []
            for match in flow_pattern.finditer(source_text):
                value = self._coerce_numeric_value(match.group('value'))
                if value is None:
                    continue
                flows.append({
                    'source': self._clean_chart_label(match.group('source')),
                    'target': self._clean_chart_label(match.group('target')),
                    'value': value,
                })
            if flows:
                return {
                    'type': 'sankey',
                    'title': title,
                    'flows': flows,
                }

        return None

    def _extract_labeled_series(self, data):
        reserved = {
            'type', 'title', 'labels', 'values', 'xAxis', 'yAxis', 'bins', 'error',
            'chart', 'data', 'items', 'segments', 'points', 'records', 'entries',
            'series', 'flows', 'links', 'payload', 'result', 'output', 'response',
            'content'
        }

        for candidate in self._walk_nested_values(data):
            if isinstance(candidate, dict):
                dict_values = list(candidate.values())
                if dict_values and all(not isinstance(value, (dict, list)) for value in dict_values):
                    scalar_pairs = []
                    for key, value in candidate.items():
                        if key in reserved:
                            continue
                        try:
                            numeric_value = float(value)
                        except (TypeError, ValueError):
                            continue
                        scalar_pairs.append((key, numeric_value))
                    if len(scalar_pairs) >= 2:
                        return [label for label, _ in scalar_pairs], [value for _, value in scalar_pairs]

                labels = None
                values = None
                for key in ['labels', 'categories', 'names', 'x', 'xValues']:
                    nested = candidate.get(key)
                    if isinstance(nested, list) and nested:
                        labels = nested
                        break
                for key in ['values', 'amounts', 'counts', 'y', 'yValues', 'totals']:
                    nested = candidate.get(key)
                    if isinstance(nested, list) and nested:
                        values = nested
                        break
                if labels and values:
                    return labels, values

            if isinstance(candidate, list) and candidate and all(isinstance(item, dict) for item in candidate):
                extracted_labels = []
                extracted_values = []
                for item in candidate:
                    label = next((item.get(k) for k in ['label', 'name', 'category', 'segment', 'x', 'title'] if item.get(k) is not None), None)
                    value = next((item.get(k) for k in ['value', 'amount', 'count', 'y', 'total', 'percent'] if item.get(k) is not None), None)
                    if label is None or value is None:
                        extracted_labels = []
                        extracted_values = []
                        break
                    extracted_labels.append(label)
                    extracted_values.append(value)
                if extracted_labels and extracted_values:
                    return extracted_labels, extracted_values
        return None, None

    def _extract_numeric_values(self, data):
        for candidate in self._walk_nested_values(data):
            if isinstance(candidate, list) and candidate:
                if all(not isinstance(item, dict) for item in candidate):
                    return candidate
                if all(isinstance(item, dict) for item in candidate):
                    extracted = []
                    for item in candidate:
                        value = next((item.get(k) for k in ['value', 'amount', 'count', 'score', 'number', 'y'] if item.get(k) is not None), None)
                        if value is None:
                            extracted = []
                            break
                        extracted.append(value)
                    if extracted:
                        return extracted
        return None

    def _extract_flows(self, data):
        for candidate in self._walk_nested_values(data):
            if not isinstance(candidate, list) or not candidate or not all(isinstance(item, dict) for item in candidate):
                continue
            flows = []
            for item in candidate:
                source = next((item.get(k) for k in ['source', 'from', 'start'] if item.get(k) is not None), None)
                target = next((item.get(k) for k in ['target', 'to', 'end'] if item.get(k) is not None), None)
                value = next((item.get(k) for k in ['value', 'amount', 'count', 'weight'] if item.get(k) is not None), None)
                if source is None or target is None or value is None:
                    flows = []
                    break
                flows.append({'source': source, 'target': target, 'value': value})
            if flows:
                return flows
        return None

    def normalize_chart_data(self, data, chart_type):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        nested_keys = [
            'chart', 'payload', 'result', 'output', 'response', 'content',
            chart_type, f'{chart_type}Chart', f'{chart_type}_chart'
        ]
        for key in nested_keys:
            nested_chart = normalized.get(key)
            if isinstance(nested_chart, dict):
                for nested_key, value in nested_chart.items():
                    normalized.setdefault(nested_key, value)

        nested_dict_values = [value for value in normalized.values() if isinstance(value, dict)]
        if len(nested_dict_values) == 1:
            for nested_key, value in nested_dict_values[0].items():
                normalized.setdefault(nested_key, value)

        normalized['type'] = str(normalized.get('type') or chart_type).strip().lower() or chart_type
        normalized['title'] = str(normalized.get('title') or f"{chart_type.title()} Chart").strip()

        if chart_type in ['bar', 'line', 'pie']:
            labels, values = self._extract_labeled_series(normalized)
            if labels is not None and values is not None:
                normalized['labels'] = labels
                normalized['values'] = values
            if chart_type in ['bar', 'line']:
                normalized.setdefault('xAxis', 'Category' if chart_type == 'bar' else 'Sequence')
                normalized.setdefault('yAxis', 'Value')

        elif chart_type == 'histogram':
            values = self._extract_numeric_values(normalized)
            if values is not None:
                normalized['values'] = values
            normalized.setdefault('bins', 10)
            normalized.setdefault('xAxis', 'Value')
            normalized.setdefault('yAxis', 'Frequency')

        elif chart_type == 'sankey':
            flows = self._extract_flows(normalized)
            if flows is not None:
                normalized['flows'] = flows

        return normalized

    def validate_chart_data(self, data, chart_type):
        """
        Validates the structure and data types of the parsed JSON object.

        Args:
            data (dict): The parsed JSON data from the LLM.
            chart_type (str): The expected type of chart.

        Returns:
            tuple[bool, str or None]: A tuple containing a boolean success flag
                                      and an error message if validation fails.
        """
        try:
            if not isinstance(data, dict):
                return False, "Chart payload must be a JSON object"
            data['type'] = str(data.get('type') or chart_type).strip().lower()
            if data['type'] != chart_type:
                data['type'] = chart_type
            data['title'] = str(data.get('title') or f"{chart_type.title()} Chart").strip()

            if chart_type == 'sankey':
                if 'flows' not in data:
                    return False, "Missing required field for sankey chart: flows"
                if not isinstance(data.get('flows'), list):
                    return False, "'flows' must be a list of objects"
                if not data['flows']:
                    return False, "Sankey charts require at least one flow"
                for i, flow in enumerate(data['flows']):
                    if not all(k in flow for k in ['source', 'target', 'value']):
                        return False, f"Flow item at index {i} is missing a source, target, or value."
                    if not isinstance(flow['value'], (int, float)):
                        return False, f"Flow value at index {i} must be a number."
                    if flow['value'] <= 0:
                        return False, f"Flow value at index {i} must be greater than zero."
                    if not str(flow['source']).strip() or not str(flow['target']).strip():
                        return False, f"Flow item at index {i} must have a non-empty source and target."
            
            elif chart_type == 'histogram':
                if 'values' not in data:
                    return False, "Missing required field for histogram: values"
                data.setdefault('bins', 10)
                data.setdefault('xAxis', 'Value')
                data.setdefault('yAxis', 'Frequency')
                if not isinstance(data['bins'], (int, float)):
                    return False, "Bins must be a number"
                if int(data['bins']) < 2:
                    return False, "Histogram bins must be at least 2"
                if not isinstance(data.get('values'), list) or len(data['values']) < 2:
                    return False, "Histogram charts require at least two values"
                    
            elif chart_type in ['bar', 'line']:
                if 'labels' not in data or 'values' not in data:
                    return False, f"Missing required fields for {chart_type} chart: labels and values"
                data.setdefault('xAxis', 'Category' if chart_type == 'bar' else 'Sequence')
                data.setdefault('yAxis', 'Value')
                if not isinstance(data.get('labels', []), list):
                    return False, "Labels must be a list"
                if len(data['labels']) != len(data['values']):
                    return False, "Labels and values must have the same length"
                if not data['labels']:
                    return False, f"{chart_type.title()} charts require at least one labeled value"
                if chart_type == 'line' and len(data['values']) < 2:
                    return False, "Line charts require at least two data points"
                if any(not str(label).strip() for label in data['labels']):
                    return False, "Labels must be non-empty strings"
                    
            elif chart_type == 'pie':
                if 'labels' not in data or 'values' not in data:
                    return False, "Missing required fields for pie chart: labels and values"
                if not isinstance(data.get('labels', []), list):
                    return False, "Labels must be a list"
                if len(data['labels']) != len(data['values']):
                    return False, "Labels and values must have the same length"
                if not data['labels']:
                    return False, "Pie charts require at least one labeled value"
                if any(not str(label).strip() for label in data['labels']):
                    return False, "Labels must be non-empty strings"
            
            # Ensure all 'values' are numeric for non-Sankey charts.
            if chart_type != 'sankey':
                try:
                    if isinstance(data['values'], list):
                        data['values'] = [float(v) for v in data['values']]
                except (ValueError, TypeError):
                    return False, "All values must be numeric"
                if chart_type == 'pie' and any(v <= 0 for v in data['values']):
                    return False, "Pie chart values must be greater than zero"
                if chart_type == 'histogram':
                    data['bins'] = max(2, int(data['bins']))
                     
            return True, None
             
        except Exception as e:
            return False, f"Validation error: {str(e)}"

    def process_sankey_data(self, raw_data):
        """
        Converts the LLM's 'flows' format for Sankey diagrams into the format
        required by the Matplotlib Sankey library (nodes and links with indices).

        Args:
            raw_data (list[dict]): The list of flow dictionaries from the LLM.

        Returns:
            dict: A dictionary containing 'nodes' and 'links' lists for plotting.
        
        Raises:
            ValueError: If the raw data cannot be processed.
        """
        try:
            # Extract unique node names from all sources and targets.
            nodes = []
            node_indices = {}
            
            for flow in raw_data:
                for node_name in [str(flow['source']), str(flow['target'])]:
                    if node_name not in node_indices:
                        node_indices[node_name] = len(nodes)
                        nodes.append({"name": node_name})
                        
            # Create links using the integer indices of the nodes.
            links = [
                {
                    "source": node_indices[str(flow['source'])],
                    "target": node_indices[str(flow['target'])],
                    "value": flow['value']
                }
                for flow in raw_data
            ]
            
            return {
                "nodes": nodes,
                "links": links
            }
        except Exception as e:
            raise ValueError(f"Error processing Sankey data: {str(e)}")

    def repair_chart_data(self, source_text, raw_payload, chart_type, kwargs):
        messages = [
            {'role': 'system', 'content': self._build_chart_repair_prompt(chart_type)},
            {
                'role': 'user',
                'content': (
                    f"Chart type: {chart_type}\n\n"
                    f"--- SOURCE TEXT ---\n{source_text}\n\n"
                    f"--- MALFORMED OR NON-CANONICAL PAYLOAD ---\n{raw_payload}\n\n"
                    "Rewrite this into the exact final schema."
                ),
            },
        ]

        response = api_provider.chat(task=config.TASK_CHART, messages=messages, **kwargs)
        repaired_response = self.clean_response(response['message']['content'])
        repaired_data = json.loads(repaired_response)
        return self.normalize_chart_data(repaired_data, chart_type)

    def _format_chart_debug_error(self, error_message, raw_payload):
        excerpt = " ".join(str(raw_payload).split())
        excerpt = excerpt[:500]
        return f"{error_message}\nRaw payload excerpt: {excerpt}"

    def get_response(self, text, chart_type):
        """
        Extracts chart data from a given text for a specific chart type.

        Args:
            text (str): The natural language text containing data.
            chart_type (str): The type of chart to generate (e.g., 'bar', 'line').

        Returns:
            str: A JSON string of the chart data, or a JSON string with an error message.
        """
        if chart_type not in self.CHART_PROMPTS:
            return json.dumps({"error": f"Invalid chart type specified: {chart_type}"})

        system_prompt = self._build_chart_system_prompt(chart_type)
        
        # Determine appropriate JSON-mode kwargs based on the active provider
        kwargs = {}
        if not api_provider.USE_API_MODE:
            kwargs['format'] = 'json'
        elif api_provider.API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            kwargs['response_format'] = {"type": "json_object"}
        elif api_provider.API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            kwargs['response_mime_type'] = "application/json"

        try:
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': self._build_chart_user_prompt(text, chart_type)}
            ]
            
            response = api_provider.chat(task=config.TASK_CHART, messages=messages, **kwargs)
            cleaned_response = self.clean_response(response['message']['content'])
            
            try:
                data = json.loads(cleaned_response)
            except json.JSONDecodeError:
                try:
                    data = self.repair_chart_data(text, response['message']['content'], chart_type, kwargs)
                except Exception:
                    heuristic_data = self.heuristic_chart_data(text, chart_type)
                    if heuristic_data:
                        data = heuristic_data
                    else:
                        return json.dumps({"error": "Invalid JSON response from model"})

            if isinstance(data, dict) and 'error' in data:
                heuristic_data = self.heuristic_chart_data(text, chart_type)
                if heuristic_data:
                    data = heuristic_data
                else:
                    return json.dumps({"error": str(data['error'])})

            data = self.normalize_chart_data(data, chart_type)
              
            is_valid, error_message = self.validate_chart_data(data, chart_type)
            if not is_valid:
                try:
                    repaired_data = self.repair_chart_data(text, cleaned_response, chart_type, kwargs)
                    if isinstance(repaired_data, dict) and 'error' in repaired_data:
                        heuristic_data = self.heuristic_chart_data(text, chart_type)
                        if heuristic_data:
                            data = heuristic_data
                            repaired_data = None
                        else:
                            return json.dumps({"error": str(repaired_data['error'])})
                    if repaired_data is None:
                        pass
                    else:
                        is_valid, error_message = self.validate_chart_data(repaired_data, chart_type)
                        if not is_valid:
                            heuristic_data = self.heuristic_chart_data(text, chart_type)
                            if heuristic_data:
                                data = heuristic_data
                            else:
                                return json.dumps({"error": self._format_chart_debug_error(error_message, cleaned_response)})
                        else:
                            data = repaired_data
                except Exception:
                    heuristic_data = self.heuristic_chart_data(text, chart_type)
                    if heuristic_data:
                        data = heuristic_data
                    else:
                        return json.dumps({"error": self._format_chart_debug_error(error_message, cleaned_response)})

            data = self.normalize_chart_data(data, chart_type)
            is_valid, error_message = self.validate_chart_data(data, chart_type)
            if not is_valid:
                return json.dumps({"error": self._format_chart_debug_error(error_message, cleaned_response)})
             
            # Special processing for Sankey data format.
            if chart_type == 'sankey' and 'flows' in data:
                try:
                    data['data'] = self.process_sankey_data(data['flows'])
                except ValueError as e:
                    return json.dumps({"error": str(e)})
            
            return json.dumps(data)
            
        except Exception as e:
            return json.dumps({"error": f"Data extraction failed: {str(e)}"})


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
