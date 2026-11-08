"""PHP's check of a fix's files (core.guards).

It finds the namespaces a file's PHP code declares (declares), for a
dataset to compare with those of the project and of its tests: PHP looks
a function or a constant that code names without a namespace up in the
code's own namespace first, and in the global one only when that has
none, so that one declared in a namespace of the tests, or of a library
they run, stands in for what they call. It reads the code between PHP's
tags apart from its strings, heredocs and comments, as PHP 8's lexer
splits them, once with the short open tag `<?` on and once with it off,
not as parsed code: code built at run time, as by eval or a file written
and then included, goes unseen. It also finds the functions a file
declares in a namespace (functions), whose names a dataset compares with
those PHP builds in (BUILTINS): one of a built-in's name declared in the
tests' namespace stands in for the built-in they call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import string

SOURCES = (".php",)
"""PHP's source files, which a task's copy has this module for
(bundle.guards_for)."""

_FLAGS = re.ASCII | re.IGNORECASE
_CHAR = r"A-Za-z0-9_\x80-\U0010ffff"
_LABEL = rf"[A-Za-z_\x80-\U0010ffff][{_CHAR}]*"
_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# The open tags with the short open tag off, and with it on.
_OPEN = re.compile(r"<\?(?:=|php(?=[ \t\r\n]|\Z))", _FLAGS)
_SHORT_OPEN = re.compile(r"<\?(?:=|php(?=[ \t\r\n]|\Z))?", _FLAGS)
# What starts a comment or a string, or ends the code: at the top, and
# inside a string's `{$…}` or `${…}`, which its braces end.
_TOP = re.compile(r"\?>|#(?!\[)|//|/\*|['\"`]|<<<")
_INNER = re.compile(r"#(?!\[)|//|/\*|['\"`{}]|<<<")
_LINE_END = re.compile(r"[\r\n]|\?>")
# What ends each kind of quoted string or opens code inside it.
_STRING = {
  "'": re.compile(r"\\.|'", re.S),
  '"': re.compile(r"\\.|\{\$|\$\{|\"", re.S),
  "`": re.compile(r"\\.|\{\$|\$\{|`", re.S),
}
_HEREDOC = re.compile(
  rf"<<<[ \t]*(?:({_LABEL})|\"({_LABEL})\"|'({_LABEL})')(?:\r\n|\r|\n)"
)
_KEYWORD = re.compile("namespace", _FLAGS)
_FUNCTION = re.compile("function", _FLAGS)
# What opens or closes a block or a parenthesis, or declares a namespace,
# a named function or a class-like body, whose named functions are
# methods; `enum` only as PHP's lexer takes it for the keyword, before a
# name, since a class may be named Enum.
_BLOCKS = re.compile(
  rf"(?<![{_CHAR}$\\])(?:"
  + rf"namespace(?![{_CHAR}\\])"
  + rf"(?:[ \t\r\n]+(?P<ns>\\?{_LABEL}(?:\\{_LABEL})*))?"
  + r"[ \t\r\n]*(?P<end>[;{]))"
  + rf"|function(?![{_CHAR}])[ \t\r\n]*&?[ \t\r\n]*(?P<name>{_LABEL})"
  + r"[ \t\r\n]*\("
  + rf"|(?P<kind>(?:class|interface|trait)(?![{_CHAR}])"
  + r"|enum(?=[ \t\r\n]+[A-Za-z_\x80-\U0010ffff]))"
  + r"|[{}();]",
  _FLAGS,
)
_DECLARE = re.compile(
  rf"namespace[ \t\r\n]+(\\?{_LABEL}(?:\\{_LABEL})*)" + r"[ \t\r\n]*[;{]",
  _FLAGS,
)


def _gap(text: str) -> str:
  # What stands for text that is not code: a space and its newlines.
  return " " + "\n" * text.count("\n")


def _string(text: str, i: int, quote: str) -> int:
  # The end of the string whose opening quote ends at i.
  stop = _STRING[quote]
  while (m := stop.search(text, i)) is not None:
    if m[0] == quote:
      return m.end()
    i = _inner(text, m.end()) if m[0] in ("{$", "${") else m.end()
  return len(text)


def _heredoc(text: str, h: re.Match[str]) -> int:
  # The end of the heredoc or nowdoc whose opening line h matched.
  label = h[1] or h[2] or h[3]
  end = rf"[ \t]*{re.escape(label)}(?![{_CHAR}])"
  if (m := re.compile(end).match(text, h.end())) is not None:
    return m.end()
  stops = [rf"(?:\r\n|\r|\n){end}"]
  if not h[3]:
    stops = [r"\\[^\r\n]", r"\{\$", r"\$\{", *stops]
  stop = re.compile("|".join(stops))
  i = h.end()
  while (m := stop.search(text, i)) is not None:
    if m[0] in ("{$", "${"):
      i = _inner(text, m.end())
    elif m[0].startswith("\\"):
      i = m.end()
    else:
      return m.end()
  return len(text)


def _skip(text: str, m: re.Match[str]) -> int:
  # The end of the comment or string that m starts.
  if m[0] in ("#", "//"):
    end = _LINE_END.search(text, m.end())
    return len(text) if end is None else end.start()
  if m[0] == "/*":
    end = text.find("*/", m.end())
    return len(text) if end < 0 else end + 2
  if m[0] == "<<<":
    h = _HEREDOC.match(text, m.start())
    return m.end() if h is None else _heredoc(text, h)
  return _string(text, m.end(), m[0])


def _inner(text: str, i: int) -> int:
  # The end of the code inside a string from i, after its closing brace.
  depth = 1
  while (m := _INNER.search(text, i)) is not None:
    if m[0] == "{":
      depth += 1
    elif m[0] == "}":
      depth -= 1
      if depth == 0:
        return m.end()
    i = m.end() if m[0] in "{}" else _skip(text, m)
  return len(text)


def _code(text: str, opener: re.Pattern[str]) -> str:
  # The code of text, each other part a gap; the closing tag, which ends
  # a statement, reads as `;`.
  parts: list[str] = []
  i = 0
  while (tag := opener.search(text, i)) is not None:
    parts.append(_gap(text[i : tag.end()]))
    i = tag.end()
    while (m := _TOP.search(text, i)) is not None and m[0] != "?>":
      end = _skip(text, m)
      parts += [text[i : m.start()], _gap(text[m.start() : end])]
      i = end
    if m is None:
      parts.append(text[i:])
      return "".join(parts)
    parts += [text[i : m.start()], ";"]
    i = m.end()
  parts.append(_gap(text[i:]))
  return "".join(parts)


def declares(text: str) -> list[tuple[int, int, str]]:
  r"""Return the namespaces a file's PHP code declares.

  Args:
    text: The file's text.

  Returns:
    For each declaration, `namespace <name>;` or `namespace <name> {`,
    the numbers of its first and last line, from 1, counted at `\n`
    alone, as git counts them, and the name without a leading `\`, its
    ASCII letters lowercase, as PHP compares names; each once, in order.

  Raises:
    RecursionError: When strings and the code inside them nest deeper than
      Python's recursion allows, some hundreds of levels, for which there
      is no reading: a caller takes that as a finding, not as none.
  """  # noqa: DOC502
  if _KEYWORD.search(text) is None:
    return []
  found: set[tuple[int, int, str]] = set()
  for opener in (_OPEN, _SHORT_OPEN):
    code = _code(text, opener)
    for m in _DECLARE.finditer(code):
      first = code.count("\n", 0, m.start()) + 1
      last = first + m[0].count("\n")
      found.add((first, last, m[1].lstrip("\\").translate(_LOWER)))
  return sorted(found)


@dataclass
class _Scope:
  # Where a scan of code stands: its namespace, its open blocks, its depth
  # of parentheses, and that at which a class-like keyword waits for its
  # body, which opens at the next `{` there, past an anonymous class's
  # arguments.
  namespace: str = ""
  blocks: list[str] = field(default_factory=list[str])
  depth: int = 0
  pending: int | None = None

  def in_class(self) -> bool:
    return bool(self.blocks) and self.blocks[-1] == "class"

  def punctuation(self, mark: str) -> None:
    if mark in "()":
      self.depth += 1 if mark == "(" else -1
    elif mark == "{":
      opens = self.pending == self.depth
      self.blocks.append("class" if opens else "block")
      self.pending = None if opens else self.pending
    elif mark == "}":
      del self.blocks[-1:]
    elif self.pending == self.depth:
      self.pending = None


def _after_member(code: str, i: int) -> bool:
  # Whether `::` or `->` comes before i, but for spaces: `X::class` names
  # a class and opens no body.
  while i and code[i - 1] in " \t\r\n":
    i -= 1
  return code.endswith(("::", "->"), 0, i)


def _functions(code: str) -> list[tuple[int, int, str, str]]:
  # The named functions code declares outside a class-like body, each with
  # its namespace.
  found: list[tuple[int, int, str, str]] = []
  scope = _Scope()
  for m in _BLOCKS.finditer(code):
    if m["end"] is not None:
      scope.namespace = (m["ns"] or "").lstrip("\\").translate(_LOWER)
      if m["end"] == "{":
        scope.blocks.append("block")
    elif m["name"] is not None:
      scope.depth += 1
      if not scope.in_class():
        first = code.count("\n", 0, m.start()) + 1
        last = first + code.count("\n", m.start(), m.end("name"))
        name = m["name"].translate(_LOWER)
        found.append((first, last, scope.namespace, name))
    elif m["kind"] is not None:
      if not _after_member(code, m.start()):
        scope.pending = scope.depth
    else:
      scope.punctuation(m[0])
  return found


def functions(text: str) -> list[tuple[int, int, str, str]]:
  r"""Return the functions a file's PHP code declares in a namespace.

  A function declared by name outside the body of a class, an interface,
  a trait or an enum, at the top of the file or inside another function,
  a method or a block, which PHP declares in the namespace in force there
  when the code runs; not a method, nor a closure.

  Args:
    text: The file's text.

  Returns:
    For each, the numbers of the first and last line, from 1, counted at
    `\n` alone, of `function` to its name, the namespace, empty for the
    global one, and the name, both as declares writes a name; each once,
    in order.

  Raises:
    RecursionError: As declares does.
  """  # noqa: DOC502
  if _FUNCTION.search(text) is None:
    return []
  found: set[tuple[int, int, str, str]] = set()
  for opener in (_OPEN, _SHORT_OPEN):
    found.update(_functions(_code(text, opener)))
  return sorted(found)


# The functions PHP 8.3.6 builds in with no namespace, as
# get_defined_functions()["internal"] lists them on Ubuntu 24.04 with
# php8.3-cli and every other php8.3-* package of the release but cgi, fpm,
# phpdbg and dev, whose ds fails to load (read 2026-11-08): a superset of
# those a project's PHP can have, so that what the tests call is among
# them. To update it, install the same in a container of that release's
# image, run `php -r 'foreach (get_defined_functions()["internal"] as $f)
# echo $f, "\n";'`, and put here those without a `\`, sorted, wrapped at 78
# columns.
_BUILTIN_NAMES = """
_ abs acos acosh addcslashes addslashes array_change_key_case array_chunk
array_column array_combine array_count_values array_diff array_diff_assoc
array_diff_key array_diff_uassoc array_diff_ukey array_fill array_fill_keys
array_filter array_flip array_intersect array_intersect_assoc
array_intersect_key array_intersect_uassoc array_intersect_ukey array_is_list
array_key_exists array_key_first array_key_last array_keys array_map
array_merge array_merge_recursive array_multisort array_pad array_pop
array_product array_push array_rand array_reduce array_replace
array_replace_recursive array_reverse array_search array_shift array_slice
array_splice array_sum array_udiff array_udiff_assoc array_udiff_uassoc
array_uintersect array_uintersect_assoc array_uintersect_uassoc array_unique
array_unshift array_values array_walk array_walk_recursive arsort asin asinh
asort assert assert_options atan atan2 atanh base64_decode base64_encode
base_convert basename bcadd bccomp bcdiv bcmod bcmul bcpow bcpowmod bcscale
bcsqrt bcsub bin2hex bind_textdomain_codeset bindec bindtextdomain boolval
bzclose bzcompress bzdecompress bzerrno bzerror bzerrstr bzflush bzopen bzread
bzwrite cal_days_in_month cal_from_jd cal_info cal_to_jd call_user_func
call_user_func_array ceil chdir checkdate checkdnsrr chgrp chmod chop chown
chr chroot chunk_split class_alias class_exists class_implements class_parents
class_uses clearstatcache cli_get_process_title cli_set_process_title closedir
closelog collator_asort collator_compare collator_create
collator_get_attribute collator_get_error_code collator_get_error_message
collator_get_locale collator_get_sort_key collator_get_strength
collator_set_attribute collator_set_strength collator_sort
collator_sort_with_sort_keys compact connection_aborted connection_status
constant convert_uudecode convert_uuencode copy cos cosh count count_chars
crc32 crypt ctype_alnum ctype_alpha ctype_cntrl ctype_digit ctype_graph
ctype_lower ctype_print ctype_punct ctype_space ctype_upper ctype_xdigit
curl_close curl_copy_handle curl_errno curl_error curl_escape curl_exec
curl_file_create curl_getinfo curl_init curl_multi_add_handle curl_multi_close
curl_multi_errno curl_multi_exec curl_multi_getcontent curl_multi_info_read
curl_multi_init curl_multi_remove_handle curl_multi_select curl_multi_setopt
curl_multi_strerror curl_pause curl_reset curl_setopt curl_setopt_array
curl_share_close curl_share_errno curl_share_init curl_share_setopt
curl_share_strerror curl_strerror curl_unescape curl_upkeep curl_version
current date date_add date_create date_create_from_format
date_create_immutable date_create_immutable_from_format date_date_set
date_default_timezone_get date_default_timezone_set date_diff date_format
date_get_last_errors date_interval_create_from_date_string
date_interval_format date_isodate_set date_modify date_offset_get date_parse
date_parse_from_format date_sub date_sun_info date_sunrise date_sunset
date_time_set date_timestamp_get date_timestamp_set date_timezone_get
date_timezone_set datefmt_create datefmt_format datefmt_format_object
datefmt_get_calendar datefmt_get_calendar_object datefmt_get_datetype
datefmt_get_error_code datefmt_get_error_message datefmt_get_locale
datefmt_get_pattern datefmt_get_timetype datefmt_get_timezone
datefmt_get_timezone_id datefmt_is_lenient datefmt_localtime datefmt_parse
datefmt_set_calendar datefmt_set_lenient datefmt_set_pattern
datefmt_set_timezone dba_close dba_delete dba_exists dba_fetch dba_firstkey
dba_handlers dba_insert dba_key_split dba_list dba_nextkey dba_open
dba_optimize dba_popen dba_replace dba_sync dcgettext dcngettext
debug_backtrace debug_print_backtrace debug_zval_dump decbin dechex decoct
define defined deflate_add deflate_init deg2rad dgettext dir dirname
disk_free_space disk_total_space diskfreespace dl dngettext dns_check_record
dns_get_mx dns_get_record dom_import_simplexml doubleval easter_date
easter_days enchant_broker_describe enchant_broker_dict_exists
enchant_broker_free enchant_broker_free_dict enchant_broker_get_dict_path
enchant_broker_get_error enchant_broker_init enchant_broker_list_dicts
enchant_broker_request_dict enchant_broker_request_pwl_dict
enchant_broker_set_dict_path enchant_broker_set_ordering enchant_dict_add
enchant_dict_add_to_personal enchant_dict_add_to_session enchant_dict_check
enchant_dict_describe enchant_dict_get_error enchant_dict_is_added
enchant_dict_is_in_session enchant_dict_quick_check
enchant_dict_store_replacement enchant_dict_suggest end enum_exists
error_clear_last error_get_last error_log error_reporting escapeshellarg
escapeshellcmd exec exif_imagetype exif_read_data exif_tagname exif_thumbnail
exp explode expm1 extension_loaded extract face_count face_detect fclose
fdatasync fdiv feof fflush fgetc fgetcsv fgets file file_exists
file_get_contents file_put_contents fileatime filectime filegroup fileinode
filemtime fileowner fileperms filesize filetype filter_has_var filter_id
filter_input filter_input_array filter_list filter_var filter_var_array
finfo_buffer finfo_close finfo_file finfo_open finfo_set_flags floatval flock
floor flush fmod fnmatch fopen forward_static_call forward_static_call_array
fpassthru fprintf fputcsv fputs fread frenchtojd fscanf fseek fsockopen fstat
fsync ftell ftok ftp_alloc ftp_append ftp_cdup ftp_chdir ftp_chmod ftp_close
ftp_connect ftp_delete ftp_exec ftp_fget ftp_fput ftp_get ftp_get_option
ftp_login ftp_mdtm ftp_mkdir ftp_mlsd ftp_nb_continue ftp_nb_fget ftp_nb_fput
ftp_nb_get ftp_nb_put ftp_nlist ftp_pasv ftp_put ftp_pwd ftp_quit ftp_raw
ftp_rawlist ftp_rename ftp_rmdir ftp_set_option ftp_site ftp_size
ftp_ssl_connect ftp_systype ftruncate func_get_arg func_get_args func_num_args
function_exists fwrite gc_collect_cycles gc_disable gc_enable gc_enabled
gc_mem_caches gc_status gd_info gearman_bugreport gearman_client_add_options
gearman_client_add_server gearman_client_add_servers gearman_client_add_task
gearman_client_add_task_background gearman_client_add_task_high
gearman_client_add_task_high_background gearman_client_add_task_low
gearman_client_add_task_low_background gearman_client_add_task_status
gearman_client_clear_callbacks gearman_client_context gearman_client_create
gearman_client_do_background gearman_client_do_high
gearman_client_do_high_background gearman_client_do_job_handle
gearman_client_do_low gearman_client_do_low_background
gearman_client_do_normal gearman_client_do_status
gearman_client_enable_exception_handler gearman_client_error
gearman_client_get_errno gearman_client_job_status
gearman_client_job_status_by_unique_key gearman_client_options
gearman_client_ping gearman_client_remove_options gearman_client_return_code
gearman_client_run_tasks gearman_client_set_complete_callback
gearman_client_set_context gearman_client_set_created_callback
gearman_client_set_data_callback gearman_client_set_exception_callback
gearman_client_set_fail_callback gearman_client_set_options
gearman_client_set_status_callback gearman_client_set_timeout
gearman_client_set_warning_callback gearman_client_set_workload_callback
gearman_client_timeout gearman_client_wait gearman_job_function_name
gearman_job_handle gearman_job_return_code gearman_job_send_complete
gearman_job_send_data gearman_job_send_exception gearman_job_send_fail
gearman_job_send_status gearman_job_send_warning gearman_job_set_return
gearman_job_unique gearman_job_workload gearman_job_workload_size
gearman_task_data gearman_task_data_size gearman_task_denominator
gearman_task_function_name gearman_task_is_known gearman_task_is_running
gearman_task_job_handle gearman_task_numerator gearman_task_recv_data
gearman_task_return_code gearman_task_send_workload gearman_task_unique
gearman_verbose_name gearman_version gearman_worker_add_function
gearman_worker_add_options gearman_worker_add_server
gearman_worker_add_servers gearman_worker_create gearman_worker_errno
gearman_worker_error gearman_worker_grab_job gearman_worker_options
gearman_worker_ping gearman_worker_register gearman_worker_remove_options
gearman_worker_return_code gearman_worker_set_id gearman_worker_set_options
gearman_worker_set_timeout gearman_worker_timeout gearman_worker_unregister
gearman_worker_unregister_all gearman_worker_wait gearman_worker_work
get_browser get_called_class get_cfg_var get_class get_class_methods
get_class_vars get_current_user get_debug_type get_declared_classes
get_declared_interfaces get_declared_traits get_defined_constants
get_defined_functions get_defined_vars get_extension_funcs get_headers
get_html_translation_table get_include_path get_included_files
get_loaded_extensions get_mangled_object_vars get_meta_tags get_object_vars
get_parent_class get_required_files get_resource_id get_resource_type
get_resources getcwd getdate getenv gethostbyaddr gethostbyname gethostbynamel
gethostname getimagesize getimagesizefromstring getlastmod getmxrr getmygid
getmyinode getmypid getmyuid getopt getprotobyname getprotobynumber getrandmax
getrusage getservbyname getservbyport gettext gettimeofday gettype glob gmdate
gmmktime gmp_abs gmp_add gmp_and gmp_binomial gmp_clrbit gmp_cmp gmp_com
gmp_div gmp_div_q gmp_div_qr gmp_div_r gmp_divexact gmp_export gmp_fact
gmp_gcd gmp_gcdext gmp_hamdist gmp_import gmp_init gmp_intval gmp_invert
gmp_jacobi gmp_kronecker gmp_lcm gmp_legendre gmp_mod gmp_mul gmp_neg
gmp_nextprime gmp_or gmp_perfect_power gmp_perfect_square gmp_popcount gmp_pow
gmp_powm gmp_prob_prime gmp_random_bits gmp_random_range gmp_random_seed
gmp_root gmp_rootrem gmp_scan0 gmp_scan1 gmp_setbit gmp_sign gmp_sqrt
gmp_sqrtrem gmp_strval gmp_sub gmp_testbit gmp_xor gmstrftime
gnupg_adddecryptkey gnupg_addencryptkey gnupg_addsignkey
gnupg_cleardecryptkeys gnupg_clearencryptkeys gnupg_clearsignkeys
gnupg_decrypt gnupg_decryptverify gnupg_deletekey gnupg_encrypt
gnupg_encryptsign gnupg_export gnupg_getengineinfo gnupg_geterror
gnupg_geterrorinfo gnupg_getprotocol gnupg_gettrustlist gnupg_import
gnupg_init gnupg_keyinfo gnupg_listsignatures gnupg_setarmor
gnupg_seterrormode gnupg_setsignmode gnupg_sign gnupg_verify grapheme_extract
grapheme_stripos grapheme_stristr grapheme_strlen grapheme_strpos
grapheme_strripos grapheme_strrpos grapheme_strstr grapheme_substr
gregoriantojd gzclose gzcompress gzdecode gzdeflate gzencode gzeof gzfile
gzgetc gzgets gzinflate gzopen gzpassthru gzputs gzread gzrewind gzseek gztell
gzuncompress gzwrite hash hash_algos hash_copy hash_equals hash_file
hash_final hash_hkdf hash_hmac hash_hmac_algos hash_hmac_file hash_init
hash_pbkdf2 hash_update hash_update_file hash_update_stream header
header_register_callback header_remove headers_list headers_sent hebrev
hex2bin hexdec highlight_file highlight_string hrtime html_entity_decode
htmlentities htmlspecialchars htmlspecialchars_decode http_build_query
http_response_code hypot iconv iconv_get_encoding iconv_mime_decode
iconv_mime_decode_headers iconv_mime_encode iconv_set_encoding iconv_strlen
iconv_strpos iconv_strrpos iconv_substr idate idn_to_ascii idn_to_utf8
igbinary_serialize igbinary_unserialize ignore_user_abort
image_type_to_extension image_type_to_mime_type imageaffine
imageaffinematrixconcat imageaffinematrixget imagealphablending imageantialias
imagearc imagebmp imagechar imagecharup imagecolorallocate
imagecolorallocatealpha imagecolorat imagecolorclosest imagecolorclosestalpha
imagecolorclosesthwb imagecolordeallocate imagecolorexact imagecolorexactalpha
imagecolormatch imagecolorresolve imagecolorresolvealpha imagecolorset
imagecolorsforindex imagecolorstotal imagecolortransparent imageconvolution
imagecopy imagecopymerge imagecopymergegray imagecopyresampled
imagecopyresized imagecreate imagecreatefrombmp imagecreatefromgd
imagecreatefromgd2 imagecreatefromgd2part imagecreatefromgif
imagecreatefromjpeg imagecreatefrompng imagecreatefromstring
imagecreatefromtga imagecreatefromwbmp imagecreatefromwebp imagecreatefromxbm
imagecreatefromxpm imagecreatetruecolor imagecrop imagecropauto
imagedashedline imagedestroy imageellipse imagefill imagefilledarc
imagefilledellipse imagefilledpolygon imagefilledrectangle imagefilltoborder
imagefilter imageflip imagefontheight imagefontwidth imageftbbox imagefttext
imagegammacorrect imagegd imagegd2 imagegetclip imagegetinterpolation imagegif
imageinterlace imageistruecolor imagejpeg imagelayereffect imageline
imageloadfont imageopenpolygon imagepalettecopy imagepalettetotruecolor
imagepng imagepolygon imagerectangle imageresolution imagerotate
imagesavealpha imagescale imagesetbrush imagesetclip imagesetinterpolation
imagesetpixel imagesetstyle imagesetthickness imagesettile imagestring
imagestringup imagesx imagesy imagetruecolortopalette imagettfbbox
imagettftext imagetypes imagewbmp imagewebp imagexbm imap_8bit imap_alerts
imap_append imap_base64 imap_binary imap_body imap_bodystruct imap_check
imap_clearflag_full imap_close imap_create imap_createmailbox imap_delete
imap_deletemailbox imap_errors imap_expunge imap_fetch_overview imap_fetchbody
imap_fetchheader imap_fetchmime imap_fetchstructure imap_fetchtext imap_gc
imap_get_quota imap_get_quotaroot imap_getacl imap_getmailboxes
imap_getsubscribed imap_headerinfo imap_headers imap_is_open imap_last_error
imap_list imap_listmailbox imap_listscan imap_listsubscribed imap_lsub
imap_mail imap_mail_compose imap_mail_copy imap_mail_move imap_mailboxmsginfo
imap_mime_header_decode imap_msgno imap_mutf7_to_utf8 imap_num_msg
imap_num_recent imap_open imap_ping imap_qprint imap_rename imap_renamemailbox
imap_reopen imap_rfc822_parse_adrlist imap_rfc822_parse_headers
imap_rfc822_write_address imap_savebody imap_scan imap_scanmailbox imap_search
imap_set_quota imap_setacl imap_setflag_full imap_sort imap_status
imap_subscribe imap_thread imap_timeout imap_uid imap_undelete
imap_unsubscribe imap_utf7_decode imap_utf7_encode imap_utf8
imap_utf8_to_mutf7 implode in_array inet_ntop inet_pton inflate_add
inflate_get_read_len inflate_get_status inflate_init ini_alter ini_get
ini_get_all ini_parse_quantity ini_restore ini_set intdiv interface_exists
intl_error_name intl_get_error_code intl_get_error_message intl_is_failure
intlcal_add intlcal_after intlcal_before intlcal_clear intlcal_create_instance
intlcal_equals intlcal_field_difference intlcal_from_date_time intlcal_get
intlcal_get_actual_maximum intlcal_get_actual_minimum
intlcal_get_available_locales intlcal_get_day_of_week_type
intlcal_get_error_code intlcal_get_error_message intlcal_get_first_day_of_week
intlcal_get_greatest_minimum intlcal_get_keyword_values_for_locale
intlcal_get_least_maximum intlcal_get_locale intlcal_get_maximum
intlcal_get_minimal_days_in_first_week intlcal_get_minimum intlcal_get_now
intlcal_get_repeated_wall_time_option intlcal_get_skipped_wall_time_option
intlcal_get_time intlcal_get_time_zone intlcal_get_type
intlcal_get_weekend_transition intlcal_in_daylight_time
intlcal_is_equivalent_to intlcal_is_lenient intlcal_is_set intlcal_is_weekend
intlcal_roll intlcal_set intlcal_set_first_day_of_week intlcal_set_lenient
intlcal_set_minimal_days_in_first_week intlcal_set_repeated_wall_time_option
intlcal_set_skipped_wall_time_option intlcal_set_time intlcal_set_time_zone
intlcal_to_date_time intlgregcal_create_instance
intlgregcal_get_gregorian_change intlgregcal_is_leap_year
intlgregcal_set_gregorian_change intltz_count_equivalent_ids
intltz_create_default intltz_create_enumeration intltz_create_time_zone
intltz_create_time_zone_id_enumeration intltz_from_date_time_zone
intltz_get_canonical_id intltz_get_display_name intltz_get_dst_savings
intltz_get_equivalent_id intltz_get_error_code intltz_get_error_message
intltz_get_gmt intltz_get_id intltz_get_id_for_windows_id intltz_get_offset
intltz_get_raw_offset intltz_get_region intltz_get_tz_data_version
intltz_get_unknown intltz_get_windows_id intltz_has_same_rules
intltz_to_date_time_zone intltz_use_daylight_time intval ip2long iptcembed
iptcparse is_a is_array is_bool is_callable is_countable is_dir is_double
is_executable is_file is_finite is_float is_infinite is_int is_integer
is_iterable is_link is_long is_nan is_null is_numeric is_object is_readable
is_resource is_scalar is_soap_fault is_string is_subclass_of is_uploaded_file
is_writable is_writeable iterator_apply iterator_count iterator_to_array
jddayofweek jdmonthname jdtofrench jdtogregorian jdtojewish jdtojulian
jdtounix jewishtojd join json_decode json_encode json_last_error
json_last_error_msg json_validate juliantojd key key_exists krsort ksort
lcfirst lcg_value lchgrp lchown ldap_add ldap_add_ext ldap_bind ldap_bind_ext
ldap_close ldap_compare ldap_connect ldap_count_entries ldap_count_references
ldap_delete ldap_delete_ext ldap_dn2ufn ldap_err2str ldap_errno ldap_error
ldap_escape ldap_exop ldap_exop_passwd ldap_exop_refresh ldap_exop_sync
ldap_exop_whoami ldap_explode_dn ldap_first_attribute ldap_first_entry
ldap_first_reference ldap_free_result ldap_get_attributes ldap_get_dn
ldap_get_entries ldap_get_option ldap_get_values ldap_get_values_len ldap_list
ldap_mod_add ldap_mod_add_ext ldap_mod_del ldap_mod_del_ext ldap_mod_replace
ldap_mod_replace_ext ldap_modify ldap_modify_batch ldap_next_attribute
ldap_next_entry ldap_next_reference ldap_parse_exop ldap_parse_reference
ldap_parse_result ldap_read ldap_rename ldap_rename_ext ldap_sasl_bind
ldap_search ldap_set_option ldap_set_rebind_proc ldap_start_tls ldap_unbind
levenshtein libxml_clear_errors libxml_disable_entity_loader libxml_get_errors
libxml_get_external_entity_loader libxml_get_last_error
libxml_set_external_entity_loader libxml_set_streams_context
libxml_use_internal_errors link linkinfo locale_accept_from_http
locale_canonicalize locale_compose locale_filter_matches
locale_get_all_variants locale_get_default locale_get_display_language
locale_get_display_name locale_get_display_region locale_get_display_script
locale_get_display_variant locale_get_keywords locale_get_primary_language
locale_get_region locale_get_script locale_lookup locale_parse
locale_set_default localeconv localtime log log10 log1p long2ip lstat ltrim
mail mailparse_determine_best_xfer_encoding mailparse_msg_create
mailparse_msg_extract_part mailparse_msg_extract_part_file
mailparse_msg_extract_whole_part_file mailparse_msg_free
mailparse_msg_get_part mailparse_msg_get_part_data mailparse_msg_get_structure
mailparse_msg_parse mailparse_msg_parse_file mailparse_rfc822_parse_addresses
mailparse_stream_encode mailparse_test mailparse_uudecode_all max
mb_check_encoding mb_chr mb_convert_case mb_convert_encoding mb_convert_kana
mb_convert_variables mb_decode_mimeheader mb_decode_numericentity
mb_detect_encoding mb_detect_order mb_encode_mimeheader
mb_encode_numericentity mb_encoding_aliases mb_ereg mb_ereg_match
mb_ereg_replace mb_ereg_replace_callback mb_ereg_search mb_ereg_search_getpos
mb_ereg_search_getregs mb_ereg_search_init mb_ereg_search_pos
mb_ereg_search_regs mb_ereg_search_setpos mb_eregi mb_eregi_replace
mb_get_info mb_http_input mb_http_output mb_internal_encoding mb_language
mb_list_encodings mb_ord mb_output_handler mb_parse_str mb_preferred_mime_name
mb_regex_encoding mb_regex_set_options mb_scrub mb_send_mail mb_split
mb_str_pad mb_str_split mb_strcut mb_strimwidth mb_stripos mb_stristr
mb_strlen mb_strpos mb_strrchr mb_strrichr mb_strripos mb_strrpos mb_strstr
mb_strtolower mb_strtoupper mb_strwidth mb_substitute_character mb_substr
mb_substr_count mcrypt_create_iv mcrypt_decrypt mcrypt_enc_get_algorithms_name
mcrypt_enc_get_block_size mcrypt_enc_get_iv_size mcrypt_enc_get_key_size
mcrypt_enc_get_modes_name mcrypt_enc_get_supported_key_sizes
mcrypt_enc_is_block_algorithm mcrypt_enc_is_block_algorithm_mode
mcrypt_enc_is_block_mode mcrypt_enc_self_test mcrypt_encrypt mcrypt_generic
mcrypt_generic_deinit mcrypt_generic_init mcrypt_get_block_size
mcrypt_get_cipher_name mcrypt_get_iv_size mcrypt_get_key_size
mcrypt_list_algorithms mcrypt_list_modes mcrypt_module_close
mcrypt_module_get_algo_block_size mcrypt_module_get_algo_key_size
mcrypt_module_get_supported_key_sizes mcrypt_module_is_block_algorithm
mcrypt_module_is_block_algorithm_mode mcrypt_module_is_block_mode
mcrypt_module_open mcrypt_module_self_test md5 md5_file mdecrypt_generic
memcache_add memcache_add_server memcache_append memcache_cas memcache_close
memcache_connect memcache_debug memcache_decrement memcache_delete
memcache_flush memcache_get memcache_get_extended_stats
memcache_get_server_status memcache_get_stats memcache_get_version
memcache_increment memcache_pconnect memcache_prepend memcache_replace
memcache_set memcache_set_compress_threshold memcache_set_failure_callback
memcache_set_sasl_auth_data memcache_set_server_params memory_get_peak_usage
memory_get_usage memory_reset_peak_usage metaphone method_exists mhash
mhash_count mhash_get_block_size mhash_get_hash_name mhash_keygen_s2k
microtime mime_content_type min mkdir mktime move_uploaded_file msg_get_queue
msg_queue_exists msg_receive msg_remove_queue msg_send msg_set_queue
msg_stat_queue msgfmt_create msgfmt_format msgfmt_format_message
msgfmt_get_error_code msgfmt_get_error_message msgfmt_get_locale
msgfmt_get_pattern msgfmt_parse msgfmt_parse_message msgfmt_set_pattern
msgpack_pack msgpack_serialize msgpack_unpack msgpack_unserialize
mt_getrandmax mt_rand mt_srand mysqli_affected_rows mysqli_autocommit
mysqli_begin_transaction mysqli_change_user mysqli_character_set_name
mysqli_close mysqli_commit mysqli_connect mysqli_connect_errno
mysqli_connect_error mysqli_data_seek mysqli_debug mysqli_dump_debug_info
mysqli_errno mysqli_error mysqli_error_list mysqli_escape_string
mysqli_execute mysqli_execute_query mysqli_fetch_all mysqli_fetch_array
mysqli_fetch_assoc mysqli_fetch_column mysqli_fetch_field
mysqli_fetch_field_direct mysqli_fetch_fields mysqli_fetch_lengths
mysqli_fetch_object mysqli_fetch_row mysqli_field_count mysqli_field_seek
mysqli_field_tell mysqli_free_result mysqli_get_charset mysqli_get_client_info
mysqli_get_client_stats mysqli_get_client_version mysqli_get_connection_stats
mysqli_get_host_info mysqli_get_links_stats mysqli_get_proto_info
mysqli_get_server_info mysqli_get_server_version mysqli_get_warnings
mysqli_info mysqli_init mysqli_insert_id mysqli_kill mysqli_more_results
mysqli_multi_query mysqli_next_result mysqli_num_fields mysqli_num_rows
mysqli_options mysqli_ping mysqli_poll mysqli_prepare mysqli_query
mysqli_real_connect mysqli_real_escape_string mysqli_real_query
mysqli_reap_async_query mysqli_refresh mysqli_release_savepoint mysqli_report
mysqli_rollback mysqli_savepoint mysqli_select_db mysqli_set_charset
mysqli_set_opt mysqli_sqlstate mysqli_ssl_set mysqli_stat
mysqli_stmt_affected_rows mysqli_stmt_attr_get mysqli_stmt_attr_set
mysqli_stmt_bind_param mysqli_stmt_bind_result mysqli_stmt_close
mysqli_stmt_data_seek mysqli_stmt_errno mysqli_stmt_error
mysqli_stmt_error_list mysqli_stmt_execute mysqli_stmt_fetch
mysqli_stmt_field_count mysqli_stmt_free_result mysqli_stmt_get_result
mysqli_stmt_get_warnings mysqli_stmt_init mysqli_stmt_insert_id
mysqli_stmt_more_results mysqli_stmt_next_result mysqli_stmt_num_rows
mysqli_stmt_param_count mysqli_stmt_prepare mysqli_stmt_reset
mysqli_stmt_result_metadata mysqli_stmt_send_long_data mysqli_stmt_sqlstate
mysqli_stmt_store_result mysqli_store_result mysqli_thread_id
mysqli_thread_safe mysqli_use_result mysqli_warning_count natcasesort natsort
net_get_interfaces next ngettext nl2br nl_langinfo
normalizer_get_raw_decomposition normalizer_is_normalized normalizer_normalize
number_format numfmt_create numfmt_format numfmt_format_currency
numfmt_get_attribute numfmt_get_error_code numfmt_get_error_message
numfmt_get_locale numfmt_get_pattern numfmt_get_symbol
numfmt_get_text_attribute numfmt_parse numfmt_parse_currency
numfmt_set_attribute numfmt_set_pattern numfmt_set_symbol
numfmt_set_text_attribute oauth_get_sbs oauth_urlencode ob_clean ob_end_clean
ob_end_flush ob_flush ob_get_clean ob_get_contents ob_get_flush ob_get_length
ob_get_level ob_get_status ob_gzhandler ob_implicit_flush ob_list_handlers
ob_start octdec odbc_autocommit odbc_binmode odbc_close odbc_close_all
odbc_columnprivileges odbc_columns odbc_commit odbc_connect
odbc_connection_string_is_quoted odbc_connection_string_quote
odbc_connection_string_should_quote odbc_cursor odbc_data_source odbc_do
odbc_error odbc_errormsg odbc_exec odbc_execute odbc_fetch_array
odbc_fetch_into odbc_fetch_object odbc_fetch_row odbc_field_len
odbc_field_name odbc_field_num odbc_field_precision odbc_field_scale
odbc_field_type odbc_foreignkeys odbc_free_result odbc_gettypeinfo
odbc_longreadlen odbc_next_result odbc_num_fields odbc_num_rows odbc_pconnect
odbc_prepare odbc_primarykeys odbc_procedurecolumns odbc_procedures
odbc_result odbc_result_all odbc_rollback odbc_setoption odbc_specialcolumns
odbc_statistics odbc_tableprivileges odbc_tables opcache_compile_file
opcache_get_configuration opcache_get_status opcache_invalidate
opcache_is_script_cached opcache_reset opendir openlog
openssl_cipher_iv_length openssl_cipher_key_length openssl_cms_decrypt
openssl_cms_encrypt openssl_cms_read openssl_cms_sign openssl_cms_verify
openssl_csr_export openssl_csr_export_to_file openssl_csr_get_public_key
openssl_csr_get_subject openssl_csr_new openssl_csr_sign openssl_decrypt
openssl_dh_compute_key openssl_digest openssl_encrypt openssl_error_string
openssl_free_key openssl_get_cert_locations openssl_get_cipher_methods
openssl_get_curve_names openssl_get_md_methods openssl_get_privatekey
openssl_get_publickey openssl_open openssl_pbkdf2 openssl_pkcs12_export
openssl_pkcs12_export_to_file openssl_pkcs12_read openssl_pkcs7_decrypt
openssl_pkcs7_encrypt openssl_pkcs7_read openssl_pkcs7_sign
openssl_pkcs7_verify openssl_pkey_derive openssl_pkey_export
openssl_pkey_export_to_file openssl_pkey_free openssl_pkey_get_details
openssl_pkey_get_private openssl_pkey_get_public openssl_pkey_new
openssl_private_decrypt openssl_private_encrypt openssl_public_decrypt
openssl_public_encrypt openssl_random_pseudo_bytes openssl_seal openssl_sign
openssl_spki_export openssl_spki_export_challenge openssl_spki_new
openssl_spki_verify openssl_verify openssl_x509_check_private_key
openssl_x509_checkpurpose openssl_x509_export openssl_x509_export_to_file
openssl_x509_fingerprint openssl_x509_free openssl_x509_parse
openssl_x509_read openssl_x509_verify ord output_add_rewrite_var
output_reset_rewrite_vars pack parse_ini_file parse_ini_string parse_str
parse_url passthru password_algos password_get_info password_hash
password_needs_rehash password_verify pathinfo pclose pcntl_alarm
pcntl_async_signals pcntl_errno pcntl_exec pcntl_fork pcntl_get_last_error
pcntl_getpriority pcntl_setpriority pcntl_signal pcntl_signal_dispatch
pcntl_signal_get_handler pcntl_sigprocmask pcntl_sigtimedwait
pcntl_sigwaitinfo pcntl_strerror pcntl_unshare pcntl_wait pcntl_waitpid
pcntl_wexitstatus pcntl_wifcontinued pcntl_wifexited pcntl_wifsignaled
pcntl_wifstopped pcntl_wstopsig pcntl_wtermsig pdo_drivers pfsockopen
pg_affected_rows pg_cancel_query pg_client_encoding pg_clientencoding pg_close
pg_cmdtuples pg_connect pg_connect_poll pg_connection_busy pg_connection_reset
pg_connection_status pg_consume_input pg_convert pg_copy_from pg_copy_to
pg_dbname pg_delete pg_end_copy pg_errormessage pg_escape_bytea
pg_escape_identifier pg_escape_literal pg_escape_string pg_exec pg_execute
pg_fetch_all pg_fetch_all_columns pg_fetch_array pg_fetch_assoc
pg_fetch_object pg_fetch_result pg_fetch_row pg_field_is_null pg_field_name
pg_field_num pg_field_prtlen pg_field_size pg_field_table pg_field_type
pg_field_type_oid pg_fieldisnull pg_fieldname pg_fieldnum pg_fieldprtlen
pg_fieldsize pg_fieldtype pg_flush pg_free_result pg_freeresult pg_get_notify
pg_get_pid pg_get_result pg_getlastoid pg_host pg_insert pg_last_error
pg_last_notice pg_last_oid pg_lo_close pg_lo_create pg_lo_export pg_lo_import
pg_lo_open pg_lo_read pg_lo_read_all pg_lo_seek pg_lo_tell pg_lo_truncate
pg_lo_unlink pg_lo_write pg_loclose pg_locreate pg_loexport pg_loimport
pg_loopen pg_loread pg_loreadall pg_lounlink pg_lowrite pg_meta_data
pg_num_fields pg_num_rows pg_numfields pg_numrows pg_options
pg_parameter_status pg_pconnect pg_ping pg_port pg_prepare pg_put_line
pg_query pg_query_params pg_result pg_result_error pg_result_error_field
pg_result_seek pg_result_status pg_select pg_send_execute pg_send_prepare
pg_send_query pg_send_query_params pg_set_client_encoding
pg_set_error_context_visibility pg_set_error_verbosity pg_setclientencoding
pg_socket pg_trace pg_transaction_status pg_tty pg_unescape_bytea pg_untrace
pg_update pg_version php_ini_loaded_file php_ini_scanned_files php_sapi_name
php_strip_whitespace php_uname phpcredits phpinfo phpversion pi pinba_flush
pinba_get_data pinba_get_info pinba_hostname_set pinba_request_time_set
pinba_reset pinba_schema_set pinba_script_name_set pinba_server_name_set
pinba_tag_delete pinba_tag_get pinba_tag_set pinba_tags_get pinba_timer_add
pinba_timer_data_merge pinba_timer_data_replace pinba_timer_delete
pinba_timer_get_info pinba_timer_start pinba_timer_stop pinba_timer_tags_merge
pinba_timer_tags_replace pinba_timers_get pinba_timers_stop popen pos
posix_access posix_ctermid posix_eaccess posix_errno posix_fpathconf
posix_get_last_error posix_getcwd posix_getegid posix_geteuid posix_getgid
posix_getgrgid posix_getgrnam posix_getgroups posix_getlogin posix_getpgid
posix_getpgrp posix_getpid posix_getppid posix_getpwnam posix_getpwuid
posix_getrlimit posix_getsid posix_getuid posix_initgroups posix_isatty
posix_kill posix_mkfifo posix_mknod posix_pathconf posix_setegid posix_seteuid
posix_setgid posix_setpgid posix_setrlimit posix_setsid posix_setuid
posix_strerror posix_sysconf posix_times posix_ttyname posix_uname pow
preg_filter preg_grep preg_last_error preg_last_error_msg preg_match
preg_match_all preg_quote preg_replace preg_replace_callback
preg_replace_callback_array preg_split prev print_r printf proc_close
proc_get_status proc_nice proc_open proc_terminate property_exists
ps_add_bookmark ps_add_kerning ps_add_launchlink ps_add_ligature
ps_add_locallink ps_add_note ps_add_pdflink ps_add_weblink ps_arc ps_arcn
ps_begin_font ps_begin_glyph ps_begin_page ps_begin_pattern ps_begin_template
ps_circle ps_clip ps_close ps_close_image ps_closepath ps_closepath_stroke
ps_continue_text ps_curveto ps_delete ps_end_font ps_end_glyph ps_end_page
ps_end_pattern ps_end_template ps_fill ps_fill_stroke ps_findfont
ps_get_buffer ps_get_parameter ps_get_value ps_glyph_list ps_glyph_show
ps_glyph_width ps_hyphenate ps_include_file ps_lineto ps_makespotcolor
ps_moveto ps_new ps_open_file ps_open_image ps_open_image_file
ps_open_memory_image ps_place_image ps_rect ps_restore ps_rotate ps_save
ps_scale ps_set_border_color ps_set_border_dash ps_set_border_style
ps_set_info ps_set_parameter ps_set_text_pos ps_set_value ps_setcolor
ps_setdash ps_setflat ps_setfont ps_setlinecap ps_setlinejoin ps_setlinewidth
ps_setmiterlimit ps_setoverprintmode ps_setpolydash ps_shading
ps_shading_pattern ps_shfill ps_show ps_show2 ps_show_boxed ps_show_xy
ps_show_xy2 ps_string_geometry ps_stringwidth ps_stroke ps_symbol
ps_symbol_name ps_symbol_width ps_translate pspell_add_to_personal
pspell_add_to_session pspell_check pspell_clear_session pspell_config_create
pspell_config_data_dir pspell_config_dict_dir pspell_config_ignore
pspell_config_mode pspell_config_personal pspell_config_repl
pspell_config_runtogether pspell_config_save_repl pspell_new pspell_new_config
pspell_new_personal pspell_save_wordlist pspell_store_replacement
pspell_suggest putenv quoted_printable_decode quoted_printable_encode
quotemeta rad2deg rand random_bytes random_int range rawurldecode rawurlencode
readdir readfile readgzfile readline readline_add_history
readline_callback_handler_install readline_callback_handler_remove
readline_callback_read_char readline_clear_history
readline_completion_function readline_info readline_list_history
readline_on_new_line readline_read_history readline_redisplay
readline_write_history readlink realpath realpath_cache_get
realpath_cache_size register_shutdown_function register_tick_function rename
reset resourcebundle_count resourcebundle_create resourcebundle_get
resourcebundle_get_error_code resourcebundle_get_error_message
resourcebundle_locales restore_error_handler restore_exception_handler rewind
rewinddir rmdir round rrd_create rrd_error rrd_fetch rrd_first rrd_graph
rrd_info rrd_last rrd_lastupdate rrd_restore rrd_tune rrd_update rrd_version
rrd_xport rrdc_disconnect rsort rtrim scandir sem_acquire sem_get sem_release
sem_remove serialize session_abort session_cache_expire session_cache_limiter
session_commit session_create_id session_decode session_destroy session_encode
session_gc session_get_cookie_params session_id session_module_name
session_name session_regenerate_id session_register_shutdown session_reset
session_save_path session_set_cookie_params session_set_save_handler
session_start session_status session_unset session_write_close
set_error_handler set_exception_handler set_file_buffer set_include_path
set_time_limit setcookie setlocale setrawcookie settype sha1 sha1_file
shell_exec shm_attach shm_detach shm_get_var shm_has_var shm_put_var
shm_remove shm_remove_var shmop_close shmop_delete shmop_open shmop_read
shmop_size shmop_write show_source shuffle similar_text simplexml_import_dom
simplexml_load_file simplexml_load_string sin sinh sizeof sleep
smbclient_chmod smbclient_client_protocols smbclient_close smbclient_closedir
smbclient_creat smbclient_fstat smbclient_fstatvfs smbclient_ftruncate
smbclient_getxattr smbclient_library_version smbclient_listxattr
smbclient_lseek smbclient_mkdir smbclient_open smbclient_opendir
smbclient_option_get smbclient_option_set smbclient_read smbclient_readdir
smbclient_removexattr smbclient_rename smbclient_rmdir smbclient_setxattr
smbclient_stat smbclient_state_errno smbclient_state_free smbclient_state_init
smbclient_state_new smbclient_statvfs smbclient_unlink smbclient_utimes
smbclient_version smbclient_write snmp2_get snmp2_getnext snmp2_real_walk
snmp2_set snmp2_walk snmp3_get snmp3_getnext snmp3_real_walk snmp3_set
snmp3_walk snmp_get_quick_print snmp_get_valueretrieval snmp_read_mib
snmp_set_enum_print snmp_set_oid_numeric_print snmp_set_oid_output_format
snmp_set_quick_print snmp_set_valueretrieval snmpget snmpgetnext snmprealwalk
snmpset snmpwalk snmpwalkoid socket_accept socket_addrinfo_bind
socket_addrinfo_connect socket_addrinfo_explain socket_addrinfo_lookup
socket_atmark socket_bind socket_clear_error socket_close socket_cmsg_space
socket_connect socket_create socket_create_listen socket_create_pair
socket_export_stream socket_get_option socket_get_status socket_getopt
socket_getpeername socket_getsockname socket_import_stream socket_last_error
socket_listen socket_read socket_recv socket_recvfrom socket_recvmsg
socket_select socket_send socket_sendmsg socket_sendto socket_set_block
socket_set_blocking socket_set_nonblock socket_set_option socket_set_timeout
socket_setopt socket_shutdown socket_strerror socket_write sodium_add
sodium_base642bin sodium_bin2base64 sodium_bin2hex sodium_compare
sodium_crypto_aead_aes256gcm_decrypt sodium_crypto_aead_aes256gcm_encrypt
sodium_crypto_aead_aes256gcm_is_available sodium_crypto_aead_aes256gcm_keygen
sodium_crypto_aead_chacha20poly1305_decrypt
sodium_crypto_aead_chacha20poly1305_encrypt
sodium_crypto_aead_chacha20poly1305_ietf_decrypt
sodium_crypto_aead_chacha20poly1305_ietf_encrypt
sodium_crypto_aead_chacha20poly1305_ietf_keygen
sodium_crypto_aead_chacha20poly1305_keygen
sodium_crypto_aead_xchacha20poly1305_ietf_decrypt
sodium_crypto_aead_xchacha20poly1305_ietf_encrypt
sodium_crypto_aead_xchacha20poly1305_ietf_keygen sodium_crypto_auth
sodium_crypto_auth_keygen sodium_crypto_auth_verify sodium_crypto_box
sodium_crypto_box_keypair
sodium_crypto_box_keypair_from_secretkey_and_publickey sodium_crypto_box_open
sodium_crypto_box_publickey sodium_crypto_box_publickey_from_secretkey
sodium_crypto_box_seal sodium_crypto_box_seal_open sodium_crypto_box_secretkey
sodium_crypto_box_seed_keypair sodium_crypto_core_ristretto255_add
sodium_crypto_core_ristretto255_from_hash
sodium_crypto_core_ristretto255_is_valid_point
sodium_crypto_core_ristretto255_random
sodium_crypto_core_ristretto255_scalar_add
sodium_crypto_core_ristretto255_scalar_complement
sodium_crypto_core_ristretto255_scalar_invert
sodium_crypto_core_ristretto255_scalar_mul
sodium_crypto_core_ristretto255_scalar_negate
sodium_crypto_core_ristretto255_scalar_random
sodium_crypto_core_ristretto255_scalar_reduce
sodium_crypto_core_ristretto255_scalar_sub sodium_crypto_core_ristretto255_sub
sodium_crypto_generichash sodium_crypto_generichash_final
sodium_crypto_generichash_init sodium_crypto_generichash_keygen
sodium_crypto_generichash_update sodium_crypto_kdf_derive_from_key
sodium_crypto_kdf_keygen sodium_crypto_kx_client_session_keys
sodium_crypto_kx_keypair sodium_crypto_kx_publickey sodium_crypto_kx_secretkey
sodium_crypto_kx_seed_keypair sodium_crypto_kx_server_session_keys
sodium_crypto_pwhash sodium_crypto_pwhash_scryptsalsa208sha256
sodium_crypto_pwhash_scryptsalsa208sha256_str
sodium_crypto_pwhash_scryptsalsa208sha256_str_verify sodium_crypto_pwhash_str
sodium_crypto_pwhash_str_needs_rehash sodium_crypto_pwhash_str_verify
sodium_crypto_scalarmult sodium_crypto_scalarmult_base
sodium_crypto_scalarmult_ristretto255
sodium_crypto_scalarmult_ristretto255_base sodium_crypto_secretbox
sodium_crypto_secretbox_keygen sodium_crypto_secretbox_open
sodium_crypto_secretstream_xchacha20poly1305_init_pull
sodium_crypto_secretstream_xchacha20poly1305_init_push
sodium_crypto_secretstream_xchacha20poly1305_keygen
sodium_crypto_secretstream_xchacha20poly1305_pull
sodium_crypto_secretstream_xchacha20poly1305_push
sodium_crypto_secretstream_xchacha20poly1305_rekey sodium_crypto_shorthash
sodium_crypto_shorthash_keygen sodium_crypto_sign sodium_crypto_sign_detached
sodium_crypto_sign_ed25519_pk_to_curve25519
sodium_crypto_sign_ed25519_sk_to_curve25519 sodium_crypto_sign_keypair
sodium_crypto_sign_keypair_from_secretkey_and_publickey
sodium_crypto_sign_open sodium_crypto_sign_publickey
sodium_crypto_sign_publickey_from_secretkey sodium_crypto_sign_secretkey
sodium_crypto_sign_seed_keypair sodium_crypto_sign_verify_detached
sodium_crypto_stream sodium_crypto_stream_keygen
sodium_crypto_stream_xchacha20 sodium_crypto_stream_xchacha20_keygen
sodium_crypto_stream_xchacha20_xor sodium_crypto_stream_xchacha20_xor_ic
sodium_crypto_stream_xor sodium_hex2bin sodium_increment sodium_memcmp
sodium_memzero sodium_pad sodium_unpad solr_get_version sort soundex
spl_autoload spl_autoload_call spl_autoload_extensions spl_autoload_functions
spl_autoload_register spl_autoload_unregister spl_classes spl_object_hash
spl_object_id sprintf sqrt srand sscanf ssh2_auth_agent
ssh2_auth_hostbased_file ssh2_auth_none ssh2_auth_password
ssh2_auth_pubkey_file ssh2_connect ssh2_disconnect ssh2_exec ssh2_fetch_stream
ssh2_fingerprint ssh2_forward_accept ssh2_forward_listen
ssh2_methods_negotiated ssh2_poll ssh2_publickey_add ssh2_publickey_init
ssh2_publickey_list ssh2_publickey_remove ssh2_scp_recv ssh2_scp_send
ssh2_send_eof ssh2_sftp ssh2_sftp_chmod ssh2_sftp_lstat ssh2_sftp_mkdir
ssh2_sftp_readlink ssh2_sftp_realpath ssh2_sftp_rename ssh2_sftp_rmdir
ssh2_sftp_stat ssh2_sftp_symlink ssh2_sftp_unlink ssh2_shell ssh2_tunnel stat
stomp_abort stomp_ack stomp_begin stomp_close stomp_commit stomp_connect
stomp_connect_error stomp_error stomp_get_read_timeout stomp_get_session_id
stomp_has_frame stomp_nack stomp_read_frame stomp_send stomp_set_read_timeout
stomp_subscribe stomp_unsubscribe stomp_version str_contains str_decrement
str_ends_with str_getcsv str_increment str_ireplace str_pad str_repeat
str_replace str_rot13 str_shuffle str_split str_starts_with str_word_count
strcasecmp strchr strcmp strcoll strcspn stream_bucket_append
stream_bucket_make_writeable stream_bucket_new stream_bucket_prepend
stream_context_create stream_context_get_default stream_context_get_options
stream_context_get_params stream_context_set_default stream_context_set_option
stream_context_set_options stream_context_set_params stream_copy_to_stream
stream_filter_append stream_filter_prepend stream_filter_register
stream_filter_remove stream_get_contents stream_get_filters stream_get_line
stream_get_meta_data stream_get_transports stream_get_wrappers stream_is_local
stream_isatty stream_register_wrapper stream_resolve_include_path
stream_select stream_set_blocking stream_set_chunk_size stream_set_read_buffer
stream_set_timeout stream_set_write_buffer stream_socket_accept
stream_socket_client stream_socket_enable_crypto stream_socket_get_name
stream_socket_pair stream_socket_recvfrom stream_socket_sendto
stream_socket_server stream_socket_shutdown stream_supports_lock
stream_wrapper_register stream_wrapper_restore stream_wrapper_unregister
strftime strip_tags stripcslashes stripos stripslashes stristr strlen
strnatcasecmp strnatcmp strncasecmp strncmp strpbrk strpos strptime strrchr
strrev strripos strrpos strspn strstr strtok strtolower strtotime strtoupper
strtr strval substr substr_compare substr_count substr_replace symlink
sys_get_temp_dir sys_getloadavg syslog system tan tanh tempnam textdomain
tideways_xhprof_disable tideways_xhprof_enable tidy_access_count
tidy_clean_repair tidy_config_count tidy_diagnose tidy_error_count
tidy_get_body tidy_get_config tidy_get_error_buffer tidy_get_head
tidy_get_html tidy_get_html_ver tidy_get_opt_doc tidy_get_output
tidy_get_release tidy_get_root tidy_get_status tidy_getopt tidy_is_xhtml
tidy_is_xml tidy_parse_file tidy_parse_string tidy_repair_file
tidy_repair_string tidy_warning_count time time_nanosleep time_sleep_until
timezone_abbreviations_list timezone_identifiers_list timezone_location_get
timezone_name_from_abbr timezone_name_get timezone_offset_get timezone_open
timezone_transitions_get timezone_version_get tmpfile token_get_all token_name
touch trait_exists transliterator_create transliterator_create_from_rules
transliterator_create_inverse transliterator_get_error_code
transliterator_get_error_message transliterator_list_ids
transliterator_transliterate trigger_error trim uasort ucfirst ucwords uksort
umask uniqid unixtojd unlink unpack unregister_tick_function unserialize
uopz_add_function uopz_allow_exit uopz_call_user_func
uopz_call_user_func_array uopz_del_function uopz_flags uopz_get_exit_status
uopz_get_hook uopz_get_mock uopz_get_property uopz_get_return uopz_get_static
uopz_redefine uopz_set_hook uopz_set_mock uopz_set_property uopz_set_return
uopz_set_static uopz_undefine uopz_unset_hook uopz_unset_mock
uopz_unset_return uploadprogress_get_contents uploadprogress_get_info
urldecode urlencode use_soap_error_handler user_error usleep usort utf8_decode
utf8_encode uuid_compare uuid_create uuid_generate_md5 uuid_generate_sha1
uuid_is_null uuid_is_valid uuid_mac uuid_parse uuid_time uuid_type
uuid_unparse uuid_variant var_dump var_export version_compare vfprintf vprintf
vsprintf wordwrap xdebug_break xdebug_call_class xdebug_call_file
xdebug_call_function xdebug_call_line xdebug_code_coverage_started
xdebug_connect_to_client xdebug_debug_zval xdebug_debug_zval_stdout
xdebug_dump_superglobals xdebug_get_code_coverage xdebug_get_collected_errors
xdebug_get_function_count xdebug_get_function_stack xdebug_get_gc_run_count
xdebug_get_gc_total_collected_roots xdebug_get_gcstats_filename
xdebug_get_headers xdebug_get_monitored_functions xdebug_get_profiler_filename
xdebug_get_stack_depth xdebug_get_tracefile_name xdebug_info
xdebug_is_debugger_active xdebug_memory_usage xdebug_notify
xdebug_peak_memory_usage xdebug_print_function_stack xdebug_set_filter
xdebug_start_code_coverage xdebug_start_error_collection
xdebug_start_function_monitor xdebug_start_gcstats xdebug_start_trace
xdebug_stop_code_coverage xdebug_stop_error_collection
xdebug_stop_function_monitor xdebug_stop_gcstats xdebug_stop_trace
xdebug_time_index xdebug_var_dump xml_error_string xml_get_current_byte_index
xml_get_current_column_number xml_get_current_line_number xml_get_error_code
xml_parse xml_parse_into_struct xml_parser_create xml_parser_create_ns
xml_parser_free xml_parser_get_option xml_parser_set_option
xml_set_character_data_handler xml_set_default_handler xml_set_element_handler
xml_set_end_namespace_decl_handler xml_set_external_entity_ref_handler
xml_set_notation_decl_handler xml_set_object
xml_set_processing_instruction_handler xml_set_start_namespace_decl_handler
xml_set_unparsed_entity_decl_handler xmlrpc_decode xmlrpc_decode_request
xmlrpc_encode xmlrpc_encode_request xmlrpc_get_type xmlrpc_is_fault
xmlrpc_parse_method_descriptions xmlrpc_server_add_introspection_data
xmlrpc_server_call_method xmlrpc_server_create xmlrpc_server_destroy
xmlrpc_server_register_introspection_callback xmlrpc_server_register_method
xmlrpc_set_type xmlwriter_end_attribute xmlwriter_end_cdata
xmlwriter_end_comment xmlwriter_end_document xmlwriter_end_dtd
xmlwriter_end_dtd_attlist xmlwriter_end_dtd_element xmlwriter_end_dtd_entity
xmlwriter_end_element xmlwriter_end_pi xmlwriter_flush
xmlwriter_full_end_element xmlwriter_open_memory xmlwriter_open_uri
xmlwriter_output_memory xmlwriter_set_indent xmlwriter_set_indent_string
xmlwriter_start_attribute xmlwriter_start_attribute_ns xmlwriter_start_cdata
xmlwriter_start_comment xmlwriter_start_document xmlwriter_start_dtd
xmlwriter_start_dtd_attlist xmlwriter_start_dtd_element
xmlwriter_start_dtd_entity xmlwriter_start_element xmlwriter_start_element_ns
xmlwriter_start_pi xmlwriter_text xmlwriter_write_attribute
xmlwriter_write_attribute_ns xmlwriter_write_cdata xmlwriter_write_comment
xmlwriter_write_dtd xmlwriter_write_dtd_attlist xmlwriter_write_dtd_element
xmlwriter_write_dtd_entity xmlwriter_write_element xmlwriter_write_element_ns
xmlwriter_write_pi xmlwriter_write_raw yaml_emit yaml_emit_file yaml_parse
yaml_parse_file yaml_parse_url zend_version zip_close zip_entry_close
zip_entry_compressedsize zip_entry_compressionmethod zip_entry_filesize
zip_entry_name zip_entry_open zip_entry_read zip_open zip_read zlib_decode
zlib_encode zlib_get_coding_type
"""
BUILTINS = frozenset(_BUILTIN_NAMES.split())
