"""THE LOCAL INDEX: the user's own files, searchable by their words.

What the user works in - every Work project's folders, and the folders the
user adds - read in a process of its own at the lowest priority the
operating system has, and kept as a full-text index (SQLite FTS5) of the
words of each text file and the name of every other. No model is called to
build it or to search it. The agents search it with `search_files`; what a
search finds and they read goes to the model they run on, as every file
they read does.
"""
