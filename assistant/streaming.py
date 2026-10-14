"""Streaming helpers: pull the "reply" text out of JSON as it arrives, and format server-sent events."""
import json

ESCAPES = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}


class ReplyExtractor:
    """Feed it chunks of the model's JSON answer; it returns the decoded characters of the top-level "reply"
    string as they arrive, so the organizer sees the reply appear before the rest of the JSON is complete.

    Only the first key is streamed (the schema and prompt put "reply" first). If the answer starts with another
    key nothing is streamed and the full reply is sent once the answer is complete.
    """
    KEY = '"reply"'

    def __init__(self):
        self.buffer = ''
        self.state = 'seek'  # seek -> value -> string -> done
        self.pos = 0

    def feed(self, chunk):
        self.buffer += chunk
        out = []
        while self.pos < len(self.buffer) and self.state != 'done':
            if self.state == 'seek':
                stripped = self.buffer.lstrip()
                if not stripped:
                    return ''
                rest = stripped[1:].lstrip()
                if stripped[0] != '{' or not (self.KEY.startswith(rest) or rest.startswith(self.KEY)):
                    self.state = 'done'  # Not an object starting with "reply": nothing to stream
                    return ''
                if len(rest) < len(self.KEY):
                    return ''  # Wait for the whole key
                self.pos, self.state = self.buffer.index(self.KEY) + len(self.KEY), 'value'
            elif self.state == 'value':
                char = self.buffer[self.pos]
                self.pos += 1
                if char == '"':
                    self.state = 'string'
                elif char not in ' \t\r\n:':
                    self.state = 'done'
            elif self.state == 'string':
                char = self.buffer[self.pos]
                if char == '"':
                    self.state = 'done'
                elif char == '\\':
                    if self.pos + 1 >= len(self.buffer):
                        break  # Wait for the rest of the escape
                    code = self.buffer[self.pos + 1]
                    if code == 'u':
                        if self.pos + 6 > len(self.buffer):
                            break
                        try:
                            point = int(self.buffer[self.pos + 2:self.pos + 6], 16)
                        except ValueError:
                            point = 0xFFFD
                        if 0xD800 <= point <= 0xDBFF:
                            # An emoji or other character outside the BMP comes as a surrogate pair
                            if self.pos + 12 > len(self.buffer):
                                break
                            if self.buffer[self.pos + 6:self.pos + 8] == '\\u':
                                try:
                                    low = int(self.buffer[self.pos + 8:self.pos + 12], 16)
                                except ValueError:
                                    low = 0
                                if 0xDC00 <= low <= 0xDFFF:
                                    out.append(chr(0x10000 + ((point - 0xD800) << 10) + (low - 0xDC00)))
                                    self.pos += 12
                                    continue
                            point = 0xFFFD
                        out.append(chr(point))
                        self.pos += 6
                        continue
                    out.append(ESCAPES.get(code, code))
                    self.pos += 2
                    continue
                else:
                    out.append(char)
                self.pos += 1
        return ''.join(out)


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
