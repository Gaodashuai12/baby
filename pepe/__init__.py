#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2002-2008 ActiveState Software Inc.
# Copyright (C) 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# License: MIT License (http://www.opensource.org/licenses/mit-license.php)

"""\
Pepe: Portable multi-language preprocessor.

Module Usage:
    from pepe import preprocess
    preprocess(infile, outfile=sys.stdout, defines={}, force=0,
               keepLines=0, includePath=[], substitute=0,
               contentType=None)

The <infile> can be marked up with special preprocessor statement lines
of the form:
    <comment-prefix> <preprocessor-statement> <comment-suffix>
where the <comment-prefix/suffix> are the native comment delimiters for
that file type.


Examples
--------

HTML (*.htm, *.html) or XML (*.xml, *.kpf, *.xul) files:

    <!-- #if FOO -->
    ...
    <!-- #endif -->

Python (*.py), Perl (*.pl), Tcl (*.tcl), Ruby (*.rb), Bash (*.sh),
or make ([Mm]akefile*) files:

    # #if defined('FAV_COLOR') and FAV_COLOR == "blue"
    ...
    # #elif FAV_COLOR == "red"
    ...
    # #else
    ...
    # #endif

C (*.c, *.h), C++ (*.cpp, *.cxx, *.cc, *.h, *.hpp, *.hxx, *.hh),
Java (*.java), PHP (*.php) or C# (*.cs) files:

    // #define FAV_COLOR 'blue'
    ...
    /* #ifndef FAV_COLOR */
    ...
    // #endif

Fortran 77 (*.f) or 90/95 (*.f90) files:

    C     #if COEFF == 'var'
          ...
    C     #endif

And other languages.


Preprocessor Syntax
-------------------

- Valid statements:
    #define <var> [<value>]
    #undef <var>
    #ifdef <var>
    #ifndef <var>
    #if <expr>
    #elif <expr>
    #else
    #endif
    #error <error string>
    #include "<file>"
    #include <var>
  where <expr> is any valid Python expression.
- The expression after #if/elif may be a Python statement. It is an
  error to refer to a variable that has not been defined by a -D
  option or by an in-content #define.
- Special built-in methods for expressions:
    defined(varName)    Return true if given variable is defined.

"""

__version_info__ = (1, 1, 0)
__version__ = '.'.join(map(str, __version_info__))

import os
import sys
import types
import re


class PreprocessorError(Exception):
    def __init__(self, error_message, filename=None, line_number=None,
                 line=None):
        self.error_message = error_message
        self.filename = filename
        self.line_number = line_number
        self.line = line
        Exception.__init__(self, error_message, filename, line_number, line)

    def __str__(self):
        """\

        Usage:

            >>> assert str(PreprocessorError("whatever", filename="somefile.py", line_number=20, line="blahblah")) == "somefile.py:20: whatever"
            >>> assert str(PreprocessorError("whatever", line_number=20, line="blahblah")) == "20: whatever"
            >>> assert str(PreprocessorError("whatever", filename="somefile.py", line="blahblah")) == "somefile.py: whatever"
            >>> assert str(PreprocessorError("whatever", line="blahblah")) == "whatever"
        """
        s = ":".join([str(f) for f in [self.filename, self.line_number] if f])
        if s:
            s += ": "
        s += self.error_message
        return s

#---- global data

# Comment delimiter info.
#   A mapping of content type to a list of 2-tuples defining the line
#   prefix and suffix for a comment. Each prefix or suffix can either
#   be a string (in which case it is transformed into a pattern allowing
#   whitespace on either side) or a compiled regex.
_commentGroups = {
    "Python": [('#', '')],
    "Perl": [('#', '')],
    "PHP": [('/*', '*/'), ('//', ''), ('#', '')],
    "Ruby": [('#', '')],
    "Tcl": [('#', '')],
    "Shell": [('#', '')],
    # Allowing for CSS and JavaScript comments in XML/HTML.
    "XML": [('<!--', '-->'), ('/*', '*/'), ('//', '')],
    "HTML": [('<!--', '-->'), ('/*', '*/'), ('//', '')],
    "Makefile": [('#', '')],
    "JavaScript": [('/*', '*/'), ('//', '')],
    "CSS": [('/*', '*/')],
    "C": [('/*', '*/')],
    "C++": [('/*', '*/'), ('//', '')],
    "Java": [('/*', '*/'), ('//', '')],
    "C#": [('/*', '*/'), ('//', '')],
    "IDL": [('/*', '*/'), ('//', '')],
    "Text": [('#', '')],
    "Fortran": [(re.compile(r'^[a-zA-Z*$]\s*'), ''), ('!', '')],
    "TeX": [('%', '')],
}



#---- internal logging facility

class _Logger:
    DEBUG, INFO, WARN, ERROR, CRITICAL = range(5)

    def __init__(self, name, level=None, streamOrFileName=sys.stderr):
        self._name = name
        if level is None:
            self.level = self.WARN
        else:
            self.level = level
        if type(streamOrFileName) == types.StringType:
            self.stream = open(streamOrFileName, 'w')
            self._opennedStream = 1
        else:
            self.stream = streamOrFileName
            self._opennedStream = 0

    def __del__(self):
        if self._opennedStream:
            self.stream.close()

    def getLevel(self):
        return self.level

    def setLevel(self, level):
        self.level = level

    def _getLevelName(self, level):
        levelNameMap = {
            self.DEBUG: "DEBUG",
            self.INFO: "INFO",
            self.WARN: "WARN",
            self.ERROR: "ERROR",
            self.CRITICAL: "CRITICAL",
        }
        return levelNameMap[level]

    def isEnabled(self, level):
        return level >= self.level

    def isDebugEnabled(self): return self.isEnabled(self.DEBUG)

    def isInfoEnabled(self): return self.isEnabled(self.INFO)

    def isWarnEnabled(self): return self.isEnabled(self.WARN)

    def isErrorEnabled(self): return self.isEnabled(self.ERROR)

    def isFatalEnabled(self): return self.isEnabled(self.FATAL)

    def log(self, level, msg, *args):
        if level < self.level:
            return
        message = "%s: %s: " % (self._name, self._getLevelName(level).lower())
        message = message + (msg % args) + "\n"
        self.stream.write(message)
        self.stream.flush()

    def debug(self, msg, *args):
        self.log(self.DEBUG, msg, *args)

    def info(self, msg, *args):
        self.log(self.INFO, msg, *args)

    def warn(self, msg, *args):
        self.log(self.WARN, msg, *args)

    def error(self, msg, *args):
        self.log(self.ERROR, msg, *args)

    def fatal(self, msg, *args):
        self.log(self.CRITICAL, msg, *args)

log = _Logger("pepe", _Logger.WARN)



#---- internal support stuff

def _evaluate(expr, defines):
    """Evaluate the given expression string with the given context.

    WARNING: This runs eval() on a user string. This is unsafe.
    """
    #interpolated = _interpolate(s, defines)
    try:
        rv = eval(expr, {'defined': lambda v: v in defines}, defines)
    except Exception, ex:
        msg = str(ex)
        if msg.startswith("name '") and msg.endswith("' is not defined"):
            # A common error (at least this is presumed:) is to have
            #   defined(FOO)   instead of   defined('FOO')
            # We should give a little as to what might be wrong.
            # msg == "name 'FOO' is not defined"  -->  varName == "FOO"
            varName = msg[len("name '"):-len("' is not defined")]
            if expr.find("defined(%s)" % varName) != -1:
                # "defined(FOO)" in expr instead of "defined('FOO')"
                msg += " (perhaps you want \"defined('%s')\" instead of "\
                       "\"defined(%s)\")" % (varName, varName)
        elif msg.startswith("invalid syntax"):
            msg = "invalid syntax: '%s'" % expr
        raise PreprocessorError(msg, defines['__FILE__'], defines['__LINE__'])
    log.debug("evaluate %r -> %s (defines=%r)", expr, rv, defines)
    return rv


#---- module API

def preprocess(infile,
               outfile=sys.stdout,
               defines={},
               should_force_overwrite=False,
               should_keep_lines=False,
               include_paths=[],
               should_substitute=False,
               content_type=None,
               content_types_registry=None,
               _preprocessed_files=None):
    """\
    Preprocesses the specified file.

    :param infile:
        The input path.
    :param outfile:
        The output path or stream (default is sys.stdout).
    :param defines:
        a dictionary of defined variables that will be
        understood in preprocessor statements. Keys must be strings and,
        currently, only the truth value of any key's value matters.
    :param should_force_overwrite:
        will overwrite the given outfile if it already exists. Otherwise
        an IOError will be raise if the outfile already exists.
    :param should_keep_lines:
        will cause blank lines to be emitted for preprocessor lines
        and content lines that would otherwise be skipped.
    :param include_paths:
        is a list of directories to search for given #include
        directives. The directory of the file being processed is presumed.
    :param should_substitute:
        if true, will allow substitution of defines into emitted
        lines. (NOTE: This substitution will happen within program strings
        as well. This may not be what you expect.)
    :param content_type:
        can be used to specify the content type of the input
        file. It not given, it will be guessed.
    :param content_types_registry:
        is an instance of ContentTypesRegistry. If not specified
        a default registry will be created.
    :param _preprocessed_files:
        (for internal use only) is used to ensure files
        are not recusively preprocessed.

    :return:
        Modified dictionary of defines or raises ``PreprocessorError`` if
        an error occurred.
    """
    if _preprocessed_files is None:
        _preprocessed_files = []
    log.info("preprocess(infile=%r, outfile=%r, defines=%r, force=%r, "\
             "keepLines=%r, includePath=%r, contentType=%r, "\
             "__preprocessedFiles=%r)", infile, outfile, defines,
             should_force_overwrite,
             should_keep_lines, include_paths, content_type,
             _preprocessed_files)
    absInfile = os.path.normpath(os.path.abspath(infile))
    if absInfile in _preprocessed_files:
        raise PreprocessorError("detected recursive #include of '%s'"\
                                % infile)
    _preprocessed_files.append(os.path.abspath(infile))

    # Determine the content type and comment info for the input file.
    if content_type is None:
        registry = content_types_registry or getDefaultContentTypesRegistry()
        content_type = registry.get_content_type(infile)
        if content_type is None:
            content_type = "Text"
            log.warn("defaulting content type for '%s' to '%s'",
                     infile, content_type)
    try:
        cgs = _commentGroups[content_type]
    except KeyError:
        raise PreprocessorError("don't know comment delimiters for content "\
                                "type '%s' (file '%s')"\
                                % (content_type, infile))

    # Generate statement parsing regexes. Basic format:
    #       <comment-prefix> <preprocessor-stmt> <comment-suffix>
    #  Examples:
    #       <!-- #if foo -->
    #       ...
    #       <!-- #endif -->
    #
    #       # #if BAR
    #       ...
    #       # #else
    #       ...
    #       # #endif
    stmts = ['#\s*(?P<op>if|elif|ifdef|ifndef)\s+(?P<expr>.*?)',
             '#\s*(?P<op>else|endif)',
             '#\s*(?P<op>error)\s+(?P<error>.*?)',
             '#\s*(?P<op>define)\s+(?P<var>[^\s]*?)(\s+(?P<val>.+?))?',
             '#\s*(?P<op>undef)\s+(?P<var>[^\s]*?)',
             '#\s*(?P<op>include)\s+"(?P<fname>.*?)"',
             r'#\s*(?P<op>include)\s+(?P<var>[^\s]+?)',
    ]
    patterns = []
    for stmt in stmts:
        # The comment group prefix and suffix can either be just a
        # string or a compiled regex.
        for cprefix, csuffix in cgs:
            if hasattr(cprefix, "pattern"):
                pattern = cprefix.pattern
            else:
                pattern = r"^\s*%s\s*" % re.escape(cprefix)
            pattern += stmt
            if hasattr(csuffix, "pattern"):
                pattern += csuffix.pattern
            else:
                pattern += r"\s*%s\s*$" % re.escape(csuffix)
            patterns.append(pattern)
    stmtRes = [re.compile(p) for p in patterns]

    # Process the input file.
    # (Would be helpful if I knew anything about lexing and parsing
    # simple grammars.)
    fin = open(infile, 'r')
    lines = fin.readlines()
    fin.close()
    if type(outfile) in types.StringTypes:
        if should_force_overwrite and os.path.exists(outfile):
            os.chmod(outfile, 0777)
            os.remove(outfile)
        fout = open(outfile, 'w')
    else:
        fout = outfile

    defines['__FILE__'] = infile
    SKIP, EMIT = range(2) # states
    states = [(EMIT, # a state is (<emit-or-skip-lines-in-this-section>,
               0, #             <have-emitted-in-this-if-block>,
               0)]     #             <have-seen-'else'-in-this-if-block>)
    lineNum = 0
    for line in lines:
        lineNum += 1
        log.debug("line %d: %r", lineNum, line)
        defines['__LINE__'] = lineNum

        # Is this line a preprocessor stmt line?
        #XXX Could probably speed this up by optimizing common case of
        #    line NOT being a preprocessor stmt line.
        for stmtRe in stmtRes:
            match = stmtRe.match(line)
            if match:
                break
        else:
            match = None

        if match:
            op = match.group("op")
            log.debug("%r stmt (states: %r)", op, states)
            if op == "define":
                if not (states and states[-1][0] == SKIP):
                    var, val = match.group("var", "val")
                    if val is None:
                        val = None
                    else:
                        try:
                            val = eval(val, {}, {})
                        except:
                            pass
                    defines[var] = val
            elif op == "undef":
                if not (states and states[-1][0] == SKIP):
                    var = match.group("var")
                    try:
                        del defines[var]
                    except KeyError:
                        pass
            elif op == "include":
                if not (states and states[-1][0] == SKIP):
                    if "var" in match.groupdict():
                        # This is the second include form: #include VAR
                        var = match.group("var")
                        f = defines[var]
                    else:
                        # This is the first include form: #include "path"
                        f = match.group("fname")

                    for d in [os.path.dirname(infile)] + include_paths:
                        fname = os.path.normpath(os.path.join(d, f))
                        if os.path.exists(fname):
                            break
                    else:
                        raise PreprocessorError(
                            "could not find #include'd file "\
                            "\"%s\" on include path: %r"\
                            % (f, include_paths))
                    defines = preprocess(fname, fout, defines,
                                         should_force_overwrite,
                                         should_keep_lines, include_paths,
                                         should_substitute,
                                         content_types_registry=content_types_registry
                                         ,
                                         _preprocessed_files=_preprocessed_files)
            elif op in ("if", "ifdef", "ifndef"):
                if op == "if":
                    expr = match.group("expr")
                elif op == "ifdef":
                    expr = "defined('%s')" % match.group("expr")
                elif op == "ifndef":
                    expr = "not defined('%s')" % match.group("expr")
                try:
                    if states and states[-1][0] == SKIP:
                        # Were are nested in a SKIP-portion of an if-block.
                        states.append((SKIP, 0, 0))
                    elif _evaluate(expr, defines):
                        states.append((EMIT, 1, 0))
                    else:
                        states.append((SKIP, 0, 0))
                except KeyError:
                    raise PreprocessorError("use of undefined variable in "\
                                            "#%s stmt" % op, defines['__FILE__']
                                            ,
                                            defines['__LINE__'], line)
            elif op == "elif":
                expr = match.group("expr")
                try:
                    if states[-1][2]: # already had #else in this if-block
                        raise PreprocessorError("illegal #elif after #else in "\
                                                "same #if block",
                                                defines['__FILE__'],
                                                defines['__LINE__'], line)
                    elif states[-1][1]: # if have emitted in this if-block
                        states[-1] = (SKIP, 1, 0)
                    elif states[:-1] and states[-2][0] == SKIP:
                        # Were are nested in a SKIP-portion of an if-block.
                        states[-1] = (SKIP, 0, 0)
                    elif _evaluate(expr, defines):
                        states[-1] = (EMIT, 1, 0)
                    else:
                        states[-1] = (SKIP, 0, 0)
                except IndexError:
                    raise PreprocessorError("#elif stmt without leading #if "\
                                            "stmt", defines['__FILE__'],
                                            defines['__LINE__'], line)
            elif op == "else":
                try:
                    if states[-1][2]: # already had #else in this if-block
                        raise PreprocessorError("illegal #else after #else in "\
                                                "same #if block",
                                                defines['__FILE__'],
                                                defines['__LINE__'], line)
                    elif states[-1][1]: # if have emitted in this if-block
                        states[-1] = (SKIP, 1, 1)
                    elif states[:-1] and states[-2][0] == SKIP:
                        # Were are nested in a SKIP-portion of an if-block.
                        states[-1] = (SKIP, 0, 1)
                    else:
                        states[-1] = (EMIT, 1, 1)
                except IndexError:
                    raise PreprocessorError("#else stmt without leading #if "\
                                            "stmt", defines['__FILE__'],
                                            defines['__LINE__'], line)
            elif op == "endif":
                try:
                    states.pop()
                except IndexError:
                    raise PreprocessorError("#endif stmt without leading #if"\
                                            "stmt", defines['__FILE__'],
                                            defines['__LINE__'], line)
            elif op == "error":
                if not (states and states[-1][0] == SKIP):
                    error = match.group("error")
                    raise PreprocessorError("#error: " + error,
                                            defines['__FILE__'],
                                            defines['__LINE__'], line)
            log.debug("states: %r", states)
            if should_keep_lines:
                fout.write("\n")
        else:
            try:
                if states[-1][0] == EMIT:
                    log.debug("emit line (%s)" % states[-1][1])
                    # Substitute all defines into line.
                    # XXX Should avoid recursive substitutions. But that
                    #     would be a pain right now.
                    sline = line
                    if should_substitute:
                        for name in reversed(sorted(defines, key=len)):
                            value = defines[name]
                            sline = sline.replace(name, str(value))
                    fout.write(sline)
                elif should_keep_lines:
                    log.debug("keep blank line (%s)" % states[-1][1])
                    fout.write("\n")
                else:
                    log.debug("skip line (%s)" % states[-1][1])
            except IndexError:
                raise PreprocessorError("superfluous #endif before this line",
                                        defines['__FILE__'],
                                        defines['__LINE__'])
    if len(states) > 1:
        raise PreprocessorError("unterminated #if block", defines['__FILE__'],
                                defines['__LINE__'])
    elif len(states) < 1:
        raise PreprocessorError("superfluous #endif on or before this line",
                                defines['__FILE__'], defines['__LINE__'])

    if fout != outfile:
        fout.close()

    return defines


#---- content-type handling

DEFAULT_CONTENT_TYPES = """
    # Default file types understood by "pepe.py".
    #
    # Format is an extension of 'mime.types' file syntax.
    #   - '#' indicates a comment to the end of the line.
    #   - a line is:
    #       <file type> [<pattern>...]
    #     where,
    #       <file type>'s are equivalent in spirit to the names used in the Windows
    #           registry in HKCR, but some of those names suck or are inconsistent;
    #           and
    #       <pattern> is a suffix (pattern starts with a '.'), a regular expression
    #           (pattern is enclosed in '/' characters), a full filename (anything
    #           else).
    #
    # Notes on case-sensitivity:
    #
    # A suffix pattern is case-insensitive on Windows and case-sensitive
    # elsewhere.  A filename pattern is case-sensitive everywhere. A regex
    # pattern's case-sensitivity is defined by the regex. This means it is by
    # default case-sensitive, but this can be changed using Python's inline
    # regex option syntax. E.g.:
    #         Makefile            /^(?i)makefile.*$/   # case-INsensitive regex

    Python              .py
    Python              .pyw
    Perl                .pl
    Ruby                .rb
    Tcl                 .tcl
    XML                 .xml
    XML                 .kpf
    XML                 .xul
    XML                 .rdf
    XML                 .xslt
    XML                 .xsl
    XML                 .wxs
    XML                 .wxi
    HTML                .htm
    HTML                .html
    XML                 .xhtml
    Makefile            /^[Mm]akefile.*$/
    PHP                 .php
    JavaScript          .js
    CSS                 .css
    C++                 .c       # C++ because then we can use //-style comments
    C++                 .cpp
    C++                 .cxx
    C++                 .cc
    C++                 .h
    C++                 .hpp
    C++                 .hxx
    C++                 .hh
    IDL                 .idl
    Text                .txt
    Fortran             .f
    Fortran             .f90
    Shell               .sh
    Shell               .csh
    Shell               .ksh
    Shell               .zsh
    Java                .java
    C#                  .cs
    TeX                 .tex

    # Some Komodo-specific file extensions
    Python              .ksf  # Fonts & Colors scheme files
    Text                .kkf  # Keybinding schemes files
"""

class ContentTypesRegistry:
    """A class that handles determining the file type of a given path.

    Usage:
        >>> registry = ContentTypesRegistry()
        >>> assert registry.get_content_type("pepe.py") == "Python"
    """

    def __init__(self, content_types_config_files=None):
        self.content_types_config_files = content_types_config_files or []
        self._load()

    def _load(self):
        from os.path import dirname, join, exists

        self.suffixMap = {}
        self.regexMap = {}
        self.filenameMap = {}

        self._loadContentType(DEFAULT_CONTENT_TYPES)
        localContentTypesPath = join(dirname(__file__), "content.types")
        if exists(localContentTypesPath):
            log.debug("load content types file: `%r'" % localContentTypesPath)
            self._loadContentType(open(localContentTypesPath, 'r').read())
        for path in self.content_types_config_files:
            log.debug("load content types file: `%r'" % path)
            self._loadContentType(open(path, 'r').read())

    def _loadContentType(self, content, path=None):
        """Return the registry for the given content.types file.

        The registry is three mappings:
            <suffix> -> <content type>
            <regex> -> <content type>
            <filename> -> <content type>
        """
        for line in content.splitlines(0):
            words = line.strip().split()
            for i in range(len(words)):
                if words[i][0] == '#':
                    del words[i:]
                    break
            if not words: continue
            contentType, patterns = words[0], words[1:]
            if not patterns:
                if line[-1] == '\n': line = line[:-1]
                raise PreprocessorError("bogus content.types line, there must "\
                                        "be one or more patterns: '%s'" % line)
            for pattern in patterns:
                if pattern.startswith('.'):
                    if sys.platform.startswith("win"):
                        # Suffix patterns are case-insensitive on Windows.
                        pattern = pattern.lower()
                    self.suffixMap[pattern] = contentType
                elif pattern.startswith('/') and pattern.endswith('/'):
                    self.regexMap[re.compile(pattern[1:-1])] = contentType
                else:
                    self.filenameMap[pattern] = contentType

    def get_content_type(self, path):
        """Return a content type for the given path.

        @param path {str} The path of file for which to guess the
            content type.
        @returns {str|None} Returns None if could not determine the
            content type.
        """
        basename = os.path.basename(path)
        contentType = None
        # Try to determine from the path.
        if not contentType and self.filenameMap.has_key(basename):
            contentType = self.filenameMap[basename]
            log.debug("Content type of '%s' is '%s' (determined from full "\
                      "path).", path, contentType)
            # Try to determine from the suffix.
        if not contentType and '.' in basename:
            suffix = "." + basename.split(".")[-1]
            if sys.platform.startswith("win"):
                # Suffix patterns are case-insensitive on Windows.
                suffix = suffix.lower()
            if self.suffixMap.has_key(suffix):
                contentType = self.suffixMap[suffix]
                log.debug("Content type of '%s' is '%s' (determined from "\
                          "suffix '%s').", path, contentType, suffix)
                # Try to determine from the registered set of regex patterns.
        if not contentType:
            for regex, ctype in self.regexMap.items():
                if regex.search(basename):
                    contentType = ctype
                    log.debug(
                        "Content type of '%s' is '%s' (matches regex '%s')",
                        path, contentType, regex.pattern)
                    break
                    # Try to determine from the file contents.
        content = open(path, 'rb').read()
        if content.startswith("<?xml"):  # cheap XML sniffing
            contentType = "XML"
        return contentType

_gDefaultContentTypesRegistry = None

def getDefaultContentTypesRegistry():
    global _gDefaultContentTypesRegistry
    if _gDefaultContentTypesRegistry is None:
        _gDefaultContentTypesRegistry = ContentTypesRegistry()
    return _gDefaultContentTypesRegistry


def parse_command_line():
    """\
    Parses the command line and returns a ``Namespace`` object
    containing options and their values.

    :return:
        A ``Namespace`` object containing options and their values.
    """

    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument('-v',
                        '--version',
                        action='version',
                        version='%(prog)s ' + __version__,
                        help="Show version number and exit.")
    parser.add_argument('input_file',
                        metavar='INFILE',
                        type=str,
                        help='Path of the input file to be preprocessed')
    parser.add_argument('-V',
                        '--verbose',
                        dest='should_be_verbose',
                        action='store_true',
                        default=False,
                        help="Enables verbose logging")
    parser.add_argument('-l',
                        '--log-level',
                        '--logging-level',
                        dest='logging_level',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR',
                                 'CRITICAL'],
                        default='INFO',
                        help="Logging level.")
    parser.add_argument('-o',
                        '--output',
                        dest='output_file',
                        default=sys.stdout,
                        help='Output file name (default STDOUT)')
    parser.add_argument('-f',
                        '--force',
                        dest='should_force_overwrite',
                        action='store_true',
                        default=False,
                        help='Force overwrite existing output file.')
    parser.add_argument('-D',
                        '--define',
                        metavar="DEFINITION",
                        dest='definitions',
                        action='append',
                        help="""\
Define a variable for preprocessing. <define>
can simply be a variable name (in which case it
will be true) or it can be of the form
<var>=<val>. An attempt will be made to convert
<val> to an integer so -D 'FOO=0' will create a
false value.""")
    parser.add_argument('-I',
                        '--include',
                        dest='include_paths',
                        action='append',
                        default=['.'],
                        help='Add a directory to the include path for #include directives.')
    parser.add_argument('-k',
                        '--keep-lines',
                        dest='should_keep_lines',
                        action='store_true',
                        default=False,
                        help='''\
Emit empty lines for preprocessor statement
lines and skipped output lines. This allows line
numbers to stay constant.''')
    parser.add_argument('-s',
                        '--substitute',
                        dest='should_substitute',
                        action='store_true',
                        default=False,
                        help='''\
Substitute #defines into emitted lines.
(Disabled by default to avoid polluting strings''')
    parser.add_argument('-c',
                        '--content-types-path',
                        '--content-types-config',
                        dest='content_types_config_files',
                        action='append',
                        help="""\
Specify a path to a content.types file to assist
with file type determination. See the
`_gDefaultContentTypes` string in this file for
details on its format.""")
    return parser.parse_args()


def parse_int_token(token):
    """\
    Parses a string to convert it to an integer based on the format used:

    :param token:
        The string to convert to an integer.
    :type token:
        ``str``
    :return:
        ``int`` or raises ``ValueError`` exception.

    Usage::

        >>> parse_int_token("0x40")
        64
        >>> parse_int_token("040")
        32
        >>> parse_int_token("40")
        40
        >>> parse_int_token("foobar")
        Traceback (most recent call last):
            ...
        ValueError: invalid literal for int() with base 10: 'foobar'
    """
    if token.startswith("0x") or token.startswith("0X"):
        return int(token, 16)
    elif token.startswith("0"):
        return int(token, 8)
    else:
        return int(token)


def parse_bool_token(token):
    """\
    Parses a string token to convert it to its equivalent boolean value ignoring
    the case of the string token or leaves the token intact if it cannot.

    :param token:
        String to convert to ``True`` or ``False``.
    :type token:
        ``str``
    :return:
        ``True`` or ``False`` or the token itself if not converted.

    Usage::

        >>> parse_bool_token('FAlse')
        False
        >>> parse_bool_token('FalS')
        'FalS'
        >>> parse_bool_token('true')
        True
        >>> parse_bool_token('TRUE')
        True
    """
    return {'true': True, 'false': False}.get(token.lower(), token)


def parse_number_token(token):
    """\
    Parses a number token to convert it to a float or int.
    Caveat: Float values like 2e-23 will not be parsed.

    :param token:
        String token to be converted.
    :type token:
        ``str``
    :return:
        ``float`` or ``int`` or raises a ``ValueError`` if a parse error
        occurred.
    """
    return float(token) if '.' in token else parse_int_token(token)


def parse_definition_expr(expr, default_value=None):
    """\
    Parses a definition expression and returns a key-value pair
    as a tuple.

    Each definition expression should be in one of these two formats:

        * <variable>=<value>
        * <variable>

    :param expr:
        String expression to be parsed.
    :param default_value:
        (Default None) When a definition is encountered that has no value, this
        will be used as its value.
    :return:
        A (define, value) tuple

        or raises a ``ValueError`` if an invalid
        definition expression is provided.

        or raises ``AttributeError`` if None is provided for ``expr``.

    Usage:

        >>> parse_definition_expr('DEBUG=1')
        ('DEBUG', 1)
        >>> parse_definition_expr('FOOBAR=0x40')
        ('FOOBAR', 64)
        >>> parse_definition_expr('FOOBAR=whatever')
        ('FOOBAR', 'whatever')
        >>> parse_definition_expr('FOOBAR=false')
        ('FOOBAR', False)
        >>> parse_definition_expr('FOOBAR=TRUE')
        ('FOOBAR', True)
        >>> parse_definition_expr('FOOBAR', default_value=None)
        ('FOOBAR', None)
        >>> parse_definition_expr('FOOBAR', default_value=1)
        ('FOOBAR', 1)
        >>> parse_definition_expr('FOOBAR=ah=3')
        ('FOOBAR', 'ah=3')
        >>> parse_definition_expr(' FOOBAR=ah=3 ')
        ('FOOBAR', 'ah=3 ')
        >>> parse_definition_expr(" ")
        Traceback (most recent call last):
            ...
        ValueError: Invalid definition symbol ` `
        >>> parse_definition_expr(None)
        Traceback (most recent call last):
            ...
        AttributeError: 'NoneType' object has no attribute 'split'
    """
    try:
        define, value = expr.split('=', 1)
        try:
            value = parse_number_token(value)
        except ValueError:
            value = parse_bool_token(value)
    except ValueError:
        if expr:
            define, value = expr, default_value
        else:
            raise ValueError("Invalid definition expression `%s`" % str(expr))
    d = define.strip()
    if d:
        return d, value
    else:
        raise ValueError("Invalid definition symbol `%s`" % str(define))



def parse_definitions(definitions):
    """\
    Parses a list of macro definitions and returns a "symbol table"
    as a dictionary.

    :params definitions:
        A list of command line macro definitions.
        Each item in the list should be in one of these two formats:

            * <variable>=<value>
            * <variable>
    :return:
        ``dict`` as symbol table or raises an exception thrown by
        :func:``parse_definition_expr``.

    Usage::

        >>> parse_definitions(['DEBUG=1'])
        {'DEBUG': 1}
        >>> parse_definitions(['FOOBAR=0x40', 'DEBUG=false'])
        {'DEBUG': False, 'FOOBAR': 64}
        >>> parse_definitions(['FOOBAR=whatever'])
        {'FOOBAR': 'whatever'}
        >>> parse_definitions(['FOOBAR'])
        {'FOOBAR': None}
        >>> parse_definitions(['FOOBAR=ah=3'])
        {'FOOBAR': 'ah=3'}
        >>> parse_definitions(None)
        {}
        >>> parse_definitions([])
        {}
    """
    defines = {}
    if definitions:
        for definition in definitions:
            define, value = parse_definition_expr(definition, default_value=None)
            defines[define] = value
    return defines


def main():
    """\
    Entry-point function.
    """
    args = parse_command_line()

    defines = parse_definitions(args.definitions)

    if args.should_be_verbose:
        log.setLevel(log.DEBUG)

    try:
        content_types_registry = ContentTypesRegistry(
            args.content_types_config_files)
        preprocess(args.input_file,
                   args.output_file,
                   defines,
                   args.should_force_overwrite,
                   args.should_keep_lines,
                   args.include_paths,
                   args.should_substitute,
                   content_types_registry=content_types_registry)
    except PreprocessorError, ex:
        if log.isDebugEnabled():
            import traceback

            traceback.print_exc(file=sys.stderr)
        else:
            sys.stderr.write("pepe: error: %s\n" % str(ex))
        return 1

if __name__ == "__main__":
    main()

