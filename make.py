import sublime, sublime_plugin
import os, threading, time
import subprocess
import re
from . misc import *
from . import parser


class LaTeXSqThread(threading.Thread):

    # pass caller to make output and killing possible
    def __init__(self, caller):
        self.caller = caller
        threading.Thread.__init__(self)

    def run(self):
        # print("Thread " + self.getName())
        t = time.time()
        caller = self.caller
        plat = sublime.platform()
        my_env = os.environ.copy()
        if caller.path: my_env["PATH"] = caller.path
        tex_dir = os.path.dirname(caller.file_name)

        if caller.cmd[0] == "latexmk":
            # check if perl is installed
            if not check_program(["perl", "-v"], my_env) and not check_program(["runscript", "tlperl", "-v"], my_env):
                sublime.error_message("Cannot find Perl.")
                return
            # check if latexmk is installed
            if not check_program(["latexmk", "-v"], my_env):
                sublime.error_message("Cannot find latexmk.")
                return

        caller.output("[Compling " + caller.file_name + "]\n")
        print(caller.cmd)
        sublime.set_timeout(caller.status_updater, 100)

        if plat == "windows":
            # make sure console does not come up
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            proc = subprocess.Popen(caller.cmd, startupinfo=startupinfo, env=my_env, cwd=tex_dir)
        else:
            proc = subprocess.Popen(caller.cmd, env=my_env, cwd=tex_dir)

        # export proc in case it needs to be killed
        self.proc = proc
        # wait until proc finishes
        proc.wait()
        if hasattr(self, 'killed') and self.killed:
            caller.output("\n[Process killed!]\n")
            return

        caller.clearoutput()
        caller.output_log(proc.returncode)
        elapsed = (time.time() - t)
        caller.output("\n[Done in %ss!]\n"% round(elapsed,2) )


class LatexsqBuildCommand(sublime_plugin.WindowCommand):
    def run(self, force=False):
        view = self.window.active_view()
        if view.is_dirty():
            print("saving...")
            view.run_command('save')

        # Get parameters for Thread:
        self.file_name = get_tex_root(view)
        tex_dir = os.path.dirname(self.file_name)

        s = view.settings()
        cmd = s.get("cmd_force") if force else s.get("cmd")
        self.cmd = cmd + [os.path.relpath(self.file_name, tex_dir)]
        os_settings = s.get(sublime.platform())
        self.path = os.path.expandvars(os_settings['path']) if 'path' in os_settings else None

        self.output_view = self.window.get_output_panel("exec")
        self.output_view.set_read_only(True)
        self.output_view.settings().set("result_file_regex", "^(?:W|E|F|B):\\s(.*):([0-9]+)\\s+")
        self.output_view.settings().set("result_base_dir", tex_dir)

        if s.get("show_panel_on_build", False):
            self.window.run_command("show_panel", {"panel": "output.exec"})

        # kill process if process exists
        if hasattr(self, 'thread') and self.thread.isAlive():
            self.output("[Process is running!]\n")
            self.output("\n[Killing running process!]\n")
            self.thread.proc.kill()
            self.thread.killed = True
            return

        self.thread = LaTeXSqThread(self)
        self.thread.start()

    def status_updater(self, status=0):
        status = status % 14
        before = min(status, 14-status)
        after = 7 - before
        self.window.active_view().set_status("latexsq", "Compling [%s=%s]" % (" " * before, " " * after))
        if self.thread and self.thread.isAlive():
            sublime.set_timeout(lambda: self.status_updater(status+1), 100)
        else:
            self.window.active_view().erase_status("latexsq")

    def output(self, data):
        self.output_view.run_command("latexsq_output", {"characters": data})

    def clearoutput(self):
        self.output_view = self.window.get_output_panel("exec")

    def output_log(self, returncode):
        view = self.window.active_view()
        tex_dir = os.path.dirname(self.file_name)
        logfile = os.path.splitext(self.file_name)[0] + ".log"
        if not os.path.isfile(logfile):
            print("Cannot find log file: %s!" % logfile)
            return

        check = parser.LogCheck()
        check.read(logfile)
        D  = check.parse()
        errors = []
        badboxes = []
        warnings = []
        fspecifiers = []

        # changing workdir for getting relative path
        old_cwd = os.getcwd()
        os.chdir(tex_dir)
        cleanfile = lambda f: os.path.relpath(re.sub("^\"", "", f.replace("/","\\")) \
                                    if sublime.platform()=="windows" else f, tex_dir)
        for d in D:
            out = (cleanfile(d['file']), int(d['line']) if 'line' in d and d['line'] else 0, d['text'])
            if 'kind' in d:
                if d['kind'] == "error":
                    errors.append("E: %s:%-4d  %s"% out)
                elif d['kind'] == "warning" and ('Underfull' in d['text'] or 'Overfull' in d['text']):
                    badboxes.append("B: %s:%-4d  %s"% out)
                elif d['kind'] == "warning" and 'float specifier changed' in d['text']:
                    fspecifiers.append("F: %s:%-4d  %s"% out)
                elif d['kind'] == "warning":
                    warnings.append("W: %s:%-4d  %s"% out)
        os.chdir(old_cwd)

        if returncode!=0 or errors:
            self.output("Complication Failure with return code [%d]!\n" % returncode)
        else:
            self.output("Complication Success!\n")

        self.output("\n"+ str(len(errors)) + " Erorr(s), " + str(len(warnings)) +
                     " Warning(s), " + str(len(fspecifiers)) + " FSC, and " +
                         str(len(badboxes)) + " BadBox(es)" + ".\n")

        if errors:
            self.output("\n[Error(s)]\n" + "\n".join(errors) + "\n")
        if warnings:
            self.output("\n[Warning(s)]\n" + "\n".join(warnings)+ "\n")
        if fspecifiers:
            self.output("\n[FSC]\n" + "\n".join(fspecifiers)+ "\n")
        if badboxes:
            self.output("\n[BadBox(es)]\n" + "\n".join(badboxes)+ "\n")

        if returncode==0 and not errors and view.settings().get("forward_sync_on_success", True):
            self.window.active_view().run_command("jump_to_pdf", {"bring_forward": False, "forward_sync": False})

class LatexsqOutputCommand(sublime_plugin.TextCommand):
    def run(self, edit, characters):
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), characters)
        self.view.set_read_only(True)