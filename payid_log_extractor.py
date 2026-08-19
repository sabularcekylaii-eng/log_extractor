"""
PayConnect Log Extractor
Simple UI: pick a date range (optional) and Pay ID(s), get the full
request/response block for each Pay ID pulled out of MinIO log exports.

All connection details (console URL, credentials, bucket, etc.) live in
config.json next to this script -- copy config.example.json to config.json
and fill it in before running.

Requires: pip install -r requirements.txt
Run:      python payid_log_extractor.py
"""

import os
import sys
import gzip
import json
import threading
import queue
import datetime
import concurrent.futures
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests

CONFIG_FILENAME = "config.json"


def _config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


def load_config():
    path = _config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"config.json not found at {path}.\n\n"
            f"Copy config.example.json to config.json and fill in your "
            f"console URL, username, password, and bucket before running."
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    required = ["endpoint", "username", "password", "bucket"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(f"config.json is missing: {', '.join(missing)}")

    cfg.setdefault("base_prefix", "logs/")
    cfg.setdefault("extensions", ".log,.gz,.txt")
    cfg.setdefault("block_marker", "Logging Request")
    cfg.setdefault("workers", 4)
    cfg.setdefault("out_dir", "")
    return cfg


class LogSearchApp(tk.Tk):
    def __init__(self, config):
        super().__init__()
        self.config_data = config
        self.title("PayConnect Log Extractor")
        self.geometry("560x560")
        self.minsize(500, 500)

        self._setup_style()

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.stop_requested = False
        self._write_lock = threading.Lock()

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------------- Style ----------------

    def _setup_style(self):
        bg = "#f4f6f8"
        accent = "#2f6fed"
        self.configure(bg=bg)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=bg, font=("Segoe UI", 10, "bold"), foreground="#20303f")
        style.configure("TLabel", background=bg, font=("Segoe UI", 9))
        style.configure("Hint.TLabel", background=bg, font=("Segoe UI", 8), foreground="#7a8a99")
        style.configure("TEntry", padding=4)
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=6)
        style.configure("Accent.TButton", background=accent, foreground="white")
        style.map("Accent.TButton", background=[("active", "#255dcc")])
        style.configure("Horizontal.TProgressbar", troughcolor="#dfe6ec", background=accent)

    # ---------------- UI ----------------

    def _build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text="PayConnect log extractor", font=("Segoe UI", 13, "bold"),
                  background="#f4f6f8", foreground="#20303f").pack(anchor="w")
        ttk.Label(
            header,
            text=f"{self.config_data['bucket']} @ {self.config_data['endpoint']}",
            style="Hint.TLabel",
        ).pack(anchor="w")

        form = ttk.Labelframe(outer, text="Date range (optional)", padding=10)
        form.pack(fill="x", pady=(12, 8))
        ttk.Label(form, text="From").grid(row=0, column=0, sticky="w")
        self.from_date_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.from_date_var, width=16).grid(row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(form, text="To").grid(row=0, column=2, sticky="w")
        self.to_date_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.to_date_var, width=16).grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Label(form, text="Format: YYYY-MM-DD. Leave blank to search every date.",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        payid_frm = ttk.Labelframe(outer, text="Pay ID(s)", padding=10)
        payid_frm.pack(fill="both", expand=True, pady=(0, 8))
        ttk.Label(payid_frm, text="One per line, or comma-separated.", style="Hint.TLabel").pack(anchor="w")
        self.payid_text = tk.Text(payid_frm, height=8, font=("Consolas", 10))
        self.payid_text.pack(fill="both", expand=True, pady=(4, 0))

        out_frm = ttk.Frame(outer)
        out_frm.pack(fill="x", pady=(0, 8))
        ttk.Label(out_frm, text="Save to:").pack(side="left")
        self.out_var = tk.StringVar(value=self.config_data.get("out_dir") or os.path.join(os.path.expanduser("~"), "Desktop"))
        ttk.Entry(out_frm, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out_frm, text="Browse...", command=self._choose_folder).pack(side="left")

        btn_frm = ttk.Frame(outer)
        btn_frm.pack(fill="x", pady=(2, 8))
        self.search_btn = ttk.Button(btn_frm, text="Search & extract", style="Accent.TButton", command=self._start_search)
        self.search_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_frm, text="Stop", command=self._stop_search, state="disabled")
        self.stop_btn.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(outer, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 6))

        log_frm = ttk.Labelframe(outer, text="Activity", padding=6)
        log_frm.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frm, state="disabled", wrap="word", font=("Consolas", 9), height=8,
                                 background="#0f1720", foreground="#d7e2ec", insertbackground="white")
        self.log_text.pack(fill="both", expand=True)

    def _choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.out_var.set(folder)

    def _log(self, msg):
        self.log_queue.put(msg)

    def _poll_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ---------------- Search logic ----------------

    def _start_search(self):
        cfg = self.config_data
        endpoint = cfg["endpoint"].rstrip("/")
        username = cfg["username"]
        password = cfg["password"]
        bucket = cfg["bucket"]
        base_prefix = cfg["base_prefix"]
        if base_prefix and not base_prefix.endswith("/"):
            base_prefix += "/"
        exts = tuple(e.strip().lower() for e in cfg["extensions"].split(",") if e.strip())
        block_marker = cfg["block_marker"]
        workers = max(1, min(16, int(cfg.get("workers", 4))))

        from_date_s = self.from_date_var.get().strip()
        to_date_s = self.to_date_var.get().strip() or from_date_s

        raw_ids = self.payid_text.get("1.0", "end").strip()
        pay_ids = [p.strip() for chunk in raw_ids.split("\n") for p in chunk.split(",") if p.strip()]
        pay_ids = sorted(set(pay_ids))

        out_dir = self.out_var.get().strip()

        if not out_dir or not pay_ids:
            messagebox.showwarning("Missing info", "Please enter at least one Pay ID and an output folder.")
            return

        from_date = to_date = None
        if from_date_s:
            try:
                from_date = datetime.date.fromisoformat(from_date_s)
                to_date = datetime.date.fromisoformat(to_date_s)
            except ValueError:
                messagebox.showerror("Bad date", "Dates must be in YYYY-MM-DD format.")
                return
            if to_date < from_date:
                messagebox.showerror("Bad date range", "To date must be on or after From date.")
                return

        os.makedirs(out_dir, exist_ok=True)

        self.search_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.start(10)
        self.stop_requested = False

        self.worker_thread = threading.Thread(
            target=self._run_search,
            args=(endpoint, username, password, bucket, base_prefix,
                  from_date, to_date, exts, pay_ids, out_dir, block_marker, workers),
            daemon=True,
        )
        self.worker_thread.start()

    def _stop_search(self):
        self.stop_requested = True
        self._log("Stop requested -- finishing in-flight files...")

    @staticmethod
    def _daterange(from_date, to_date):
        days = (to_date - from_date).days
        for i in range(days + 1):
            yield from_date + datetime.timedelta(days=i)

    def _login(self, session, endpoint, username, password):
        login_url = f"{endpoint}/api/v1/login"
        resp = session.post(login_url, json={"accessKey": username, "secretKey": password}, timeout=30)
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Login failed ({resp.status_code}) at {login_url}\n"
                f"Response body: {resp.text[:500]}"
            )
        self._log("Login OK.")

    def _list_objects(self, session, endpoint, bucket, prefix):
        list_url = f"{endpoint}/api/v1/buckets/{bucket}/objects"
        all_objects = []
        start_after = None
        page_size = None
        page_num = 0
        seen_first_names = set()

        while True:
            page_num += 1
            params = {"prefix": prefix, "recursive": "true", "with_versions": "false"}
            if start_after:
                params["start_after"] = start_after

            resp = session.get(list_url, params=params, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"List failed ({resp.status_code}) at {list_url}?prefix={prefix}\n"
                    f"Response body: {resp.text[:500]}"
                )
            try:
                data = resp.json()
            except Exception:
                self._log(f"  [debug] non-JSON body (first 400 chars): {resp.text[:400]}")
                break

            page_objects = data.get("objects", data if isinstance(data, list) else [])
            if not page_objects:
                break

            first_name = page_objects[0].get("name")
            if first_name in seen_first_names:
                break
            seen_first_names.add(first_name)
            all_objects.extend(page_objects)

            if page_num == 1:
                page_size = len(page_objects)
            if page_size is not None and len(page_objects) < page_size:
                break

            start_after = page_objects[-1].get("name")
            if page_num > 100:
                self._log("  [debug] stopping after 100 pages (safety limit).")
                break

        keys = []
        for obj in all_objects:
            name = obj.get("name") or obj.get("prefix")
            if not name or name.endswith("/") or obj.get("is_folder") or obj.get("isDir"):
                continue
            keys.append(name)
        return keys

    def _download_object(self, session, endpoint, bucket, key):
        download_url = f"{endpoint}/api/v1/buckets/{bucket}/objects/download"
        resp = session.get(
            download_url,
            params={"prefix": key, "version_id": "null"},
            timeout=120,
            stream=True,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Download failed ({resp.status_code}) at {download_url}?prefix={key}\n"
                f"Response body: {resp.text[:500]}"
            )
        return resp

    @staticmethod
    def _iter_lines(resp, key):
        """Yield decoded-bytes lines, transparently gunzipping .gz files."""
        if key.lower().endswith(".gz"):
            resp.raw.decode_content = False
            with gzip.GzipFile(fileobj=resp.raw) as gz:
                for raw_line in gz:
                    yield raw_line
        else:
            for raw_line in resp.iter_lines():
                yield raw_line

    def _flush_block(self, block_lines, key, start_line, end_line, pay_ids, match_counts, out_handles):
        """Write the whole buffered block to every Pay ID's file that appears anywhere in it."""
        if not block_lines:
            return
        matched = set()
        for pid in pay_ids:
            for bl in block_lines:
                if pid in bl:
                    matched.add(pid)
                    break
        if not matched:
            return
        with self._write_lock:
            for pid in matched:
                match_counts[pid] += 1
                handle = out_handles[pid]
                handle.write(f"===== {key} (lines {start_line}-{end_line}) =====\n")
                for bl in block_lines:
                    handle.write(bl + "\n")
                handle.write("\n")

    def _scan_file(self, session, endpoint, bucket, key, pay_ids, block_marker, out_handles, match_counts):
        resp = self._download_object(session, endpoint, bucket, key)
        line_no = 0
        block_lines = []
        block_start = 1
        for raw_line in self._iter_lines(resp, key):
            line_no += 1
            if self.stop_requested:
                break
            if raw_line is None:
                continue
            try:
                line = raw_line.decode("utf-8", errors="ignore").rstrip("\r\n")
            except Exception:
                continue

            if block_marker and block_marker in line and block_lines:
                self._flush_block(block_lines, key, block_start, line_no - 1, pay_ids, match_counts, out_handles)
                block_lines = [line]
                block_start = line_no
            else:
                block_lines.append(line)

        self._flush_block(block_lines, key, block_start, line_no, pay_ids, match_counts, out_handles)

    def _run_search(self, endpoint, username, password, bucket, base_prefix,
                     from_date, to_date, exts, pay_ids, out_dir, block_marker, workers):
        try:
            session = requests.Session()
            self._log(f"Logging in to {endpoint} ...")
            self._login(session, endpoint, username, password)

            if from_date:
                date_tag = from_date.isoformat() if from_date == to_date else f"{from_date.isoformat()}_to_{to_date.isoformat()}"
            else:
                date_tag = "alldates"

            out_paths = {}
            out_handles = {}
            for pid in pay_ids:
                safe_name = pid.replace("/", "_")
                path = os.path.join(out_dir, f"{safe_name}_{date_tag}.txt")
                out_paths[pid] = path
                out_handles[pid] = open(path, "w", encoding="utf-8")

            match_counts = {pid: 0 for pid in pay_ids}
            files_checked = 0

            if from_date:
                prefixes = [
                    f"{base_prefix}{day.year}/{day.month:02d}/{day.day:02d}/"
                    for day in self._daterange(from_date, to_date)
                ]
            else:
                prefixes = [base_prefix]

            for prefix in prefixes:
                if self.stop_requested:
                    break
                self._log(f"Listing: {bucket}/{prefix}")
                try:
                    keys = self._list_objects(session, endpoint, bucket, prefix)
                except Exception as e:
                    self._log(f"  ERROR listing {prefix}: {e}")
                    continue

                keys = [k for k in keys if not exts or k.lower().endswith(exts)]
                if not keys:
                    self._log(f"  No files found under {prefix}.")
                    continue

                self._log(f"  {len(keys)} file(s) queued -- scanning with {workers} worker(s)...")

                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_key = {
                        executor.submit(self._scan_file, session, endpoint, bucket, key,
                                         pay_ids, block_marker, out_handles, match_counts): key
                        for key in keys
                    }
                    for future in concurrent.futures.as_completed(future_to_key):
                        key = future_to_key[future]
                        files_checked += 1
                        try:
                            future.result()
                            self._log(f"  done: {key}")
                        except Exception as e:
                            self._log(f"  ERROR reading {key}: {e}")
                        if self.stop_requested:
                            break

            for pid, handle in out_handles.items():
                handle.flush()
                handle.close()

            not_found = []
            for pid in pay_ids:
                if match_counts[pid] == 0:
                    not_found.append(pid)
                    try:
                        os.remove(out_paths[pid])
                    except OSError:
                        pass

            self._log(f"Done. Checked {files_checked} file(s).")
            for pid in pay_ids:
                if match_counts[pid] > 0:
                    self._log(f"  {pid}: {match_counts[pid]} matching block(s) -> {os.path.basename(out_paths[pid])}")
                else:
                    self._log(f"  {pid}: NOT FOUND")

            if not_found:
                self.after(0, self._show_not_found, not_found)

        except Exception as e:
            self._log(f"ERROR: {e}")
        finally:
            self.progress.stop()
            self.search_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    def _show_not_found(self, not_found):
        msg = "No logs found for these Pay ID(s):\n\n" + "\n".join(not_found)
        messagebox.showinfo("Not found", msg)


def main():
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Setup needed", str(e))
        sys.exit(1)

    app = LogSearchApp(config)
    app.mainloop()


if __name__ == "__main__":
    main()
