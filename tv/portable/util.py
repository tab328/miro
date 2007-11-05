# Miro - an RSS based video player application
# Copyright (C) 2005-2007 Participatory Culture Foundation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA

import os
import random
import re
import sys
import sha
import time
import string
import urllib
import socket
import logging
import filetypes
import tempfile
import threading
import traceback
import subprocess

from clock import clock
from types import UnicodeType, StringType
from BitTorrent.bencode import bdecode, bencode

# Should we print out warning messages.  Turn off in the unit tests.
chatter = True

inDownloader = False
# this gets set to True when we're in the download process.

ignoreErrors = False

# Perform escapes needed for Javascript string contents.
def quoteJS(x):
    x = x.replace("\\", "\\\\") # \       -> \\
    x = x.replace("\"", "\\\"") # "       -> \"  
    x = x.replace("'",  "\\'")  # '       -> \'
    x = x.replace("\n", "\\n")  # newline -> \n
    x = x.replace("\r", "\\r")  # CR      -> \r
    return x

def getNiceStack():
    """Get a stack trace that's a easier to read that the full one.  """
    stack = traceback.extract_stack()
    # We don't care about the unit test lines
    while (len(stack) > 0 and
        os.path.basename(stack[0][0]) == 'unittest.py' or 
        (isinstance(stack[0][3], str) and 
            stack[0][3].startswith('unittest.main'))):
        stack = stack[1:]
    # remove after the call to util.failed
    for i in xrange(len(stack)):
        if (os.path.basename(stack[i][0]) == 'util.py' and 
                stack[i][2] in ('failed', 'failedExn')):
            stack = stack[:i+1]
            break
    # remove trapCall calls
    stack = [i for i in stack if i[2] != 'trapCall']
    return stack

# Parse a configuration file in a very simple format. Each line is
# either whitespace or "Key = Value". Whitespace is ignored at the
# beginning of Value, but the remainder of the line is taken
# literally, including any whitespace. There is no way to put a
# newline in a value. Returns the result as a dict.
def readSimpleConfigFile(path):
    ret = {}

    f = open(path, "rt")
    for line in f.readlines():
        # Skip blank lines
        if re.match("^[ \t]*$", line):
            continue

        # Otherwise it'd better be a configuration setting
        match = re.match(r"^([^ ]+) *= *([^\r\n]*)[\r\n]*$", line)
        if not match:
            print "WARNING: %s: ignored bad configuration directive '%s'" % (path, line)
            continue
        
        key = match.group(1)
        value = match.group(2)
        if key in ret:
            print "WARNING: %s: ignored duplicate directive '%s'" % (path, line)
            continue

        ret[key] = value

    return ret

# Given a dict, write a configuration file in the format that
# readSimpleConfigFile reads.
def writeSimpleConfigFile(path, data):
    f = open(path, "wt")

    for (k, v) in data.iteritems():
        f.write("%s = %s\n" % (k, v))
    
    f.close()

# Called at build-time to ask Subversion for the revision number of
# this checkout. Going to fail without Cygwin. Yeah, oh well. Pass the
# file or directory you want to use as a reference point. Returns an
# integer on success or None on failure.
def queryRevision(f):
    try:
        p = subprocess.Popen(["svn", "info", f], stdout=subprocess.PIPE) 
        info = p.stdout.read()
        p.stdout.close()
        url = re.search("URL: (.*)", info).group(1)
        url = url.strip()
        revision = re.search("Revision: (.*)", info).group(1)
        revision = revision.strip()
        return (url, revision)
    except KeyboardInterrupt:
        raise
    except:
        # whatever
        return None

# 'path' is a path that could be passed to open() to open a file on
# this platform. It must be an absolute path. Return the file:// URL
# that would refer to the same file.
def absolutePathToFileURL(path):
    if isinstance(path, unicode):
        path = path.encode("utf-8")
    parts = string.split(path, os.sep)
    parts = [urllib.quote(x, ':') for x in parts]
    return "file://" + '/'.join(parts)


# Shortcut for 'failed' with the exception flag.
def failedExn(when, **kwargs):
    failed(when, withExn = True, **kwargs)

# Puts up a dialog with debugging information encouraging the user to
# file a ticket. (Also print a call trace to stderr or whatever, which
# hopefully will end up on the console or in a log.) 'when' should be
# something like "when trying to play a video." The user will see
# it. If 'withExn' is true, last-exception information will be printed
# to. If 'detail' is true, it will be included in the report and the
# the console/log, but not presented in the dialog box flavor text.
def failed(when, withExn = False, details = None):
    logging.info ("failed() called; generating crash report.")

    header = ""
    try:
        import config # probably works at runtime only
        import prefs
        header += "App:        %s\n" % config.get(prefs.LONG_APP_NAME)
        header += "Publisher:  %s\n" % config.get(prefs.PUBLISHER)
        header += "Platform:   %s\n" % config.get(prefs.APP_PLATFORM)
        header += "Python:     %s\n" % sys.version.replace("\r\n"," ").replace("\n"," ").replace("\r"," ")
        header += "Py Path:    %s\n" % repr(sys.path)
        header += "Version:    %s\n" % config.get(prefs.APP_VERSION)
        header += "Serial:     %s\n" % config.get(prefs.APP_SERIAL)
        header += "Revision:   %s\n" % config.get(prefs.APP_REVISION)
        header += "Builder:    %s\n" % config.get(prefs.BUILD_MACHINE)
        header += "Build Time: %s\n" % config.get(prefs.BUILD_TIME)
    except KeyboardInterrupt:
        raise
    except:
        pass
    header += "Time:       %s\n" % time.asctime()
    header += "When:       %s\n" % when
    header += "\n"

    if withExn:
        header += "Exception\n---------\n"
        header += ''.join(traceback.format_exception(*sys.exc_info()))
        header += "\n"
    if details:
        header += "Details: %s\n" % (details, )
    header += "Call stack\n----------\n"
    try:
        stack = getNiceStack()
    except KeyboardInterrupt:
        raise
    except:
        stack = traceback.extract_stack()
    header += ''.join(traceback.format_list(stack))
    header += "\n"

    header += "Threads\n-------\n"
    header += "Current: %s\n" % threading.currentThread().getName()
    header += "Active:\n"
    for t in threading.enumerate():
        header += " - %s%s\n" % \
            (t.getName(),
             t.isDaemon() and ' [Daemon]' or '')

    # Combine the header with the logfile contents, if available, to
    # make the dialog box crash message. {{{ and }}} are Trac
    # Wiki-formatting markers that force a fixed-width font when the
    # report is pasted into a ticket.
    report = "{{{\n%s}}}\n" % header

    def readLog(logFile, logName="Log"):
        try:
            f = open(logFile, "rt")
            logContents = "%s\n---\n" % logName
            logContents += f.read()
            f.close()
        except KeyboardInterrupt:
            raise
        except:
            logContents = ''
        return logContents

    logFile = config.get(prefs.LOG_PATHNAME)
    downloaderLogFile = config.get(prefs.DOWNLOADER_LOG_PATHNAME)
    if logFile is None:
        logContents = "No logfile available on this platform.\n"
    else:
        logContents = readLog(logFile)
    if downloaderLogFile is not None:
        if logContents is not None:
            logContents += "\n" + readLog(downloaderLogFile, "Downloader Log")
        else:
            logContents = readLog(downloaderLogFile)

    if logContents is not None:
        report += "{{{\n%s}}}\n" % stringify(logContents)

    # Dump the header for the report we just generated to the log, in
    # case there are multiple failures or the user sends in the log
    # instead of the report from the dialog box. (Note that we don't
    # do this until we've already read the log into the dialog
    # message.)
    logging.info ("----- CRASH REPORT (DANGER CAN HAPPEN) -----")
    logging.info (header)
    logging.info ("----- END OF CRASH REPORT -----")

    if not inDownloader:
        try:
            import dialogs
            from gtcache import gettext as _
            if not ignoreErrors:
                chkboxdialog = dialogs.CheckboxTextboxDialog(_("Internal Error"),_("Miro has encountered an internal error. You can help us track down this problem and fix it by submitting an error report."), _("Include entire program database including all video and channel metadata with crash report"), False, _("Describe what you were doing that caused this error"), dialogs.BUTTON_SUBMIT_REPORT, dialogs.BUTTON_IGNORE)
                chkboxdialog.run(lambda x: _sendReport(report, x))
        except Exception, e:
            logging.exception ("Execption when reporting errror..")
    else:
        from dl_daemon import command, daemon
        c = command.DownloaderErrorCommand(daemon.lastDaemon, report)
        c.send()

def _sendReport(report, dialog):
    def callback(result):
        app.controller.sendingCrashReport -= 1
        if result['status'] != 200 or result['body'] != 'OK':
            logging.warning(u"Failed to submit crash report. Server returned %r" % result)
        else:
            logging.info(u"Crash report submitted successfully")
    def errback(error):
        app.controller.sendingCrashReport -= 1
        logging.warning(u"Failed to submit crash report %r" % error)

    import dialogs
    import httpclient
    import config
    import prefs
    import app

    global ignoreErrors
    if dialog.choice == dialogs.BUTTON_IGNORE:
        ignoreErrors = True
        return

    backupfile = None
    if hasattr(dialog,"checkbox_value") and dialog.checkbox_value:
        try:
            logging.info("Sending entire database")
            import database
            backupfile = database.defaultDatabase.liveStorage.backupDatabase()
        except:
            traceback.print_exc()
            logging.warning(u"Failed to backup database")


    description = u"Description text not implemented"
    if hasattr(dialog,"textbox_value"):
        description = dialog.textbox_value

    description = description.encode("utf-8")
    postVars = {"description":description,
                "app_name": config.get(prefs.LONG_APP_NAME),
                "log": report}
    if backupfile:
        postFiles = {"databasebackup": {"filename":"databasebackup.zip", "mimetype":"application/octet-stream", "handle":open(backupfile, "rb")}}
    else:
        postFiles = None
    app.controller.sendingCrashReport += 1
    httpclient.grabURL("http://participatoryculture.org/bogondeflector/index.php", callback, errback, method="POST", postVariables = postVars, postFiles = postFiles)

class AutoflushingStream:
    """Converts a stream to an auto-flushing one.  It behaves in exactly the
    same way, except all write() calls are automatically followed by a
    flush().
    """
    def __init__(self, stream):
        self.__dict__['stream'] = stream
    def write(self, data):
        if isinstance(data, unicode):
            data = data.encode('ascii', 'backslashreplace')
        self.stream.write(data)
        self.stream.flush()
    def __getattr__(self, name):
        return getattr(self.stream, name)
    def __setattr__(self, name, value):
        return setattr(self.stream, name, value)

def makeDummySocketPair():
    """Create a pair of sockets connected to each other on the local
    interface.  Used to implement SocketHandler.wakeup().
    """

    dummy_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dummy_server.bind( ('127.0.0.1', 0) )
    dummy_server.listen(1)
    server_address = dummy_server.getsockname()
    first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    first.connect(server_address)
    second, address = dummy_server.accept()
    dummy_server.close()
    return first, second

def trapCall(when, function, *args, **kwargs):
    """Make a call to a function, but trap any exceptions and do a failedExn
    call for them.  Return True if the function successfully completed, False
    if it threw an exception
    """

    try:
        function(*args, **kwargs)
        return True
    except KeyboardInterrupt:
        raise
    except:
        failedExn(when)
        return False

# Turn the next flag on to track the cumulative time for each when argument to
# timeTrapCall().  Don't do this for production builds though!  Since we never
# clean up the entries in the cumulative dict, turning this on amounts to a
# memory leak.
TRACK_CUMULATIVE = False 
cumulative = {}
cancel = False

def timeTrapCall(when, function, *args, **kwargs):
    global cancel
    cancel = False
    start = clock()
    retval = trapCall (when, function, *args, **kwargs)
    end = clock()
    if cancel:
        return retval
    if end-start > 1.0:
        logging.timing ("WARNING: %s too slow (%.3f secs)",
            when, end-start)
    if TRACK_CUMULATIVE:
        try:
            total = cumulative[when]
        except KeyboardInterrupt:
            raise
        except:
            total = 0
        total += end - start
        cumulative[when] = total
        return retval
        if total > 5.0:
            logging.timing ("%s cumulative is too slow (%.3f secs)",
                when, total)
            cumulative[when] = 0
    cancel = True
    return retval

def getTorrentInfoHash(path):
    f = open(path, 'rb')
    try:
        data = f.read()
        metainfo = bdecode(data)
        infohash = sha.sha(bencode(metainfo['info'])).digest()
        return infohash
    finally:
        f.close()

class ExponentialBackoffTracker:
    """Utility class to track exponential backoffs."""
    def __init__(self, baseDelay):
        self.baseDelay = self.currentDelay = baseDelay
    def nextDelay(self):
        rv = self.currentDelay
        self.currentDelay *= 2
        return rv
    def reset(self):
        self.currentDelay = self.baseDelay


# Gather movie files on the disk. Used by the startup dialog.
def gatherVideos(path, progressCallback):
    import item
    import prefs
    import config
    import platformutils
    keepGoing = True
    parsed = 0
    found = list()
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                parsed = parsed + 1
                if filetypes.isVideoFilename(f):
                    found.append(os.path.join(root, f))
                if parsed > 1000:
                    adjustedParsed = int(parsed / 100.0) * 100
                elif parsed > 100:
                    adjustedParsed = int(parsed / 10.0) * 10
                else:
                    adjustedParsed = parsed
                keepGoing = progressCallback(adjustedParsed, len(found))
                if not keepGoing:
                    found = None
                    raise
            if config.get(prefs.SHORT_APP_NAME) in dirs:
                dirs.remove(config.get(prefs.SHORT_APP_NAME))
    except KeyboardInterrupt:
        raise
    except:
        pass
    return found

def formatSizeForUser(bytes, zeroString="", withDecimals=True, kbOnly=False):
    """Format an int containing the number of bytes into a string suitable for
    printing out to the user.  zeroString is the string to use if bytes == 0.
    """
    from gtcache import gettext as _
    if bytes > (1 << 30) and not kbOnly:
        value = (bytes / (1024.0 * 1024.0 * 1024.0))
        if withDecimals:
            format = _("%1.1fGB")
        else:
            format = _("%dGB")
    elif bytes > (1 << 20) and not kbOnly:
        value = (bytes / (1024.0 * 1024.0))
        if withDecimals:
            format = _("%1.1fMB")
        else:
            format = _("%dMB")
    elif bytes > (1 << 10):
        value = (bytes / 1024.0)
        if withDecimals:
            format = _("%1.1fKB")
        else:
            format = _("%dKB")
    elif bytes > 1:
        value = bytes
        if withDecimals:
            format = _("%1.1fB")
        else:
            format = _("%dB")
    else:
        return zeroString

    return format % value

def formatTimeForUser(seconds, sign=1):
    """Format a duration in seconds into a string suitable for display, using
    the minimum amount of digits. Negative durations used for remaining times
    display a '-' sign.
    """
    _, _, _, h, m, s, _, _, _ = time.gmtime(seconds)
    if sign < 0:
        sign = '-'
    else:
        sign = ''
    if int(seconds) in range(0, 3600):
        return "%s%d:%02u" % (sign, m, s)
    else:
        return "%s%d:%02u:%02u" % (sign, h, m, s)

def makeAnchor(label, href):
    return '<a href="%s">%s</a>' % (href, label)

def makeEventURL(label, eventURL):
    return '<a href="#" onclick="return eventURL(\'action:%s\');">%s</a>' % \
            (eventURL, label)

def clampText(text, maxLength):
    if len(text) > maxLength:
        return text[:maxLength-3] + '...'
    else:
        return text

def print_mem_usage(message):
    pass
# Uncomment for memory usage printouts on linux.
#    print message
#    os.system ("ps huwwwp %d" % (os.getpid(),))

class TooManySingletonsError(Exception):
    pass

def getSingletonDDBObject(view):
    view.confirmDBThread()
    viewLength = view.len()
    if viewLength == 1:
        view.resetCursor()
        return view.next()
    elif viewLength == 0:
        raise LookupError("Can't find singleton in %s" % repr(view))
    else:
        msg = "%d objects in %s" % (viewLength, len(view))
        raise TooManySingletonsError(msg)

class ThreadSafeCounter:
    """Implements a counter that can be access by multiple threads."""
    def __init__(self, initialValue=0):
        self.value = initialValue
        self.lock = threading.Lock()

    def inc(self):
        self.lock.acquire()
        try:
            self.value += 1
        finally:
            self.lock.release()

    def dec(self):
        self.lock.acquire()
        try:
            self.value -= 1
        finally:
            self.lock.release()

    def getvalue(self):
        self.lock.acquire()
        try:
            return self.value
        finally:
            self.lock.release()

def setupLogging():
    logging.addLevelName(25, "TIMING")
    logging.timing = lambda msg, *args, **kargs: logging.log(25, msg, *args, **kargs)
    logging.addLevelName(26, "JSALERT")
    logging.jsalert = lambda msg, *args, **kargs: logging.log(26, msg, *args, **kargs)


# Returned when input to a template function isn't unicode
class DemocracyUnicodeError(StandardError):
    pass

# Raise an exception if input isn't unicode
def checkU(text):
    if text is not None and type(text) != UnicodeType:
        raise DemocracyUnicodeError, (u"text \"%s\" is not a unicode string" %
                                     text)

# Decorator that raised an exception if the function doesn't return unicode
def returnsUnicode(func):
    def checkFunc(*args, **kwargs):
        result = func(*args,**kwargs)
        if result is not None:
            checkU(result)
        return result
    return checkFunc

# Raise an exception if input isn't a binary string
def checkB(text):
    if text is not None and type(text) != StringType:
        raise DemocracyUnicodeError, (u"text \"%s\" is not a binary string" %
                                     text)

# Decorator that raised an exception if the function doesn't return unicode
def returnsBinary(func):
    def checkFunc(*args, **kwargs):
        result = func(*args,**kwargs)
        if result is not None:
            checkB(result)
        return result
    return checkFunc

# Raise an exception if input isn't a URL type
def checkURL(text):
    if type(text) != UnicodeType:
        raise DemocracyUnicodeError, (u"url \"%s\" is not unicode" %
                                     text)
    try:
        text.encode('ascii')
    except:
        raise DemocracyUnicodeError, (u"url \"%s\" contains extended characters" %
                                     text)

# Decorator that raised an exception if the function doesn't return a filename
def returnsURL(func):
    def checkFunc(*args, **kwargs):
        result = func(*args,**kwargs)
        if result is not None:
            checkURL(result)
        return result
    return checkFunc

# Returns exception if input isn't a filename type
def checkF(text):
    from platformutils import FilenameType
    if text is not None and type(text) != FilenameType:
        raise DemocracyUnicodeError, (u"text \"%s\" is not a valid filename type" %
                                     text)

# Decorator that raised an exception if the function doesn't return a filename
def returnsFilename(func):
    def checkFunc(*args, **kwargs):
        result = func(*args,**kwargs)
        if result is not None:
            checkF(result)
        return result
    return checkFunc

def unicodify(d):
    """Turns all strings in data structure to unicode.
    """
    if isinstance(d, dict):
        for key in d.keys():
            d[key] = unicodify(d[key])
    elif isinstance(d, list):
        for key in range(len(d)):
            d[key] = unicodify(d[key])
    elif type(d) == StringType:
        d = d.decode('ascii','replace')
    return d

def stringify(u, handleerror="xmlcharrefreplace"):
    """Takes a possibly unicode string and converts it to a string string.
    This is required for some logging especially where the things being
    logged are filenames which can be Unicode in the Windows platform.

    Note that this is not the inverse of unicodify.

    You can pass in a handleerror argument which defaults to "xmlcharrefreplace".
    This will increase the string size as it converts unicode characters that
    don't have ascii equivalents into escape sequences.  If you don't want to
    increase the string length, use "replace" which will use ? for unicode
    characters that don't have ascii equivalents.
    """
    if isinstance(u, unicode):
        return u.encode("ascii", handleerror)
    if not isinstance(u, str):
        return str(u)
    return u

def quoteUnicodeURL(url):
    """Quote international characters contained in a URL according to w3c, see:
    <http://www.w3.org/International/O-URL-code.html>
    """
    checkU(url)
    quotedChars = list()
    for c in url.encode('utf8'):
        if ord(c) > 127:
            quotedChars.append(urllib.quote(c))
        else:
            quotedChars.append(c)
    return u''.join(quotedChars)

def no_console_startupinfo():
    """Returns the startupinfo argument for subprocess.Popen so that we don't
    open a console window.  On platforms other than windows, this is just
    None.  On windows, it's some win32 sillyness.
    """
    if subprocess.mswindows:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return startupinfo
    else:
        return None

def call_command(*args, **kwargs):
    """Call an external command.  If the command doesn't exit with status 0,
    or if it outputs to stderr, an exception will be raised.  Returns stdout.
    """
    ignore_stderr = kwargs.pop('ignore_stderr', False)
    if kwargs:
        raise TypeError('extra keyword arguments: %s' % kwargs)

    pipe = subprocess.Popen(args, stdout=subprocess.PIPE,
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            startupinfo=no_console_startupinfo())
    stdout, stderr = pipe.communicate()
    if pipe.returncode != 0:
        raise OSError("call_command with %s has return code %s\nstdout:%s\nstderr:%s" % 
                (args, pipe.returncode, stdout, stderr))
    elif stderr and not ignore_stderr:
        raise OSError("call_command with %s outputed error text:\n%s" % 
                (args, stderr))
    else:
        return stdout

def getsize(path):
    """Get the size of a path.  If it's a file, return the size of the file.
    If it's a directory return the total size of all the files it contains.
    """

    if os.path.isdir(path):
        size = 0
        for (dirpath, dirnames, filenames) in os.walk(path):
            for name in filenames:
                size += os.path.getsize(os.path.join(dirpath, name))
            size += os.path.getsize(dirpath)
        return size
    else:
        return os.path.getsize(path)

def partition(list, size):
    """Partiction list into smaller lists such that none is larger than
    size elements.

    Returns a list of lists.  The lists appended together will be the original
    list.
    """
    retval = []
    for start in range(0, len(list), size):
        retval.append(list[start:start+size])
    return retval

def miro_listdir(directory):
    """Directory listing that's safe and convenient for finding new videos in
    a directory.

    Returns the tuple (files, directories) where both elements are a list of
    absolute pathnames.  OSErrors are silently ignored.  Hidden files aren't
    returned.  Pathnames are run through os.path.normcase.
    """

    files = []
    directories = []
    directory = os.path.abspath(os.path.normcase(directory))
    try:
        listing = os.listdir(directory)
    except OSError:
        return [], []
    for name in listing:
        if name[0] == '.' or name.lower() == 'thumbs.db':
            # thumbs.db is a windows file that speeds up thumbnails.  We know
            # it's not a movie file.
            continue
        path = os.path.join(directory, os.path.normcase(name))
        try:
            if os.path.isdir(path):
                directories.append(path)
            else:
                files.append(path)
        except OSError:
            pass
    return files, directories

def directoryWritable(directory):
    """Check if we can write to a directory."""
    try:
        f = tempfile.TemporaryFile(dir=directory)
    except OSError:
        return False
    else:
        f.close()
        return True

def random_string(length):
    return ''.join(random.choice(string.ascii_letters) for i in xrange(length))
