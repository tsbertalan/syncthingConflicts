"""
Scan the given top directory for files like vid_test.sync-conflict-20230723-000249-ONMECE6.py
and open Meld.exe with up to three files to compare.
"""

# Python standard library imports
import os, subprocess, re, threading, queue, time, hashlib
from collections import deque

# Third-party imports
from showinfm import show_in_file_manager
import send2trash
import tkinter as tk
from tkinter import ttk
use_sv = True
if use_sv:
    import sv_ttk


# Constants
CONFLICT_FILE_REGEX = re.compile(r".*sync-conflict-[0-9A-Z-]*")
def looks_like_conflictfile(filename: str) -> bool:
    """
    Check if a filename looks like a Syncthing conflict file.

    Parameters:
    filename (str): The filename to check.

    Returns:
    bool: True if the filename looks like a Syncthing conflict file, False otherwise.
    """
    return CONFLICT_FILE_REGEX.match(filename) and not (filename.startswith('.syncthing') and filename.endswith('.tmp'))


class TicksPerSecondWatcher:
    """A class to monitor the rate of an event (ticks) per second."""

    def __init__(self, smoothing_factor=10):
        self.ticks = 0
        self.last_time = time.time()
        self.last_ticks = 0
        self.ticks_per_second = 0
        self.smoothing_history = deque(maxlen=smoothing_factor)

    def __call__(self):
        self.ticks += 1
        new_time = time.time()
        time_since_last = new_time - self.last_time

        # Avoid division by zero
        if time_since_last == 0:
            return self.ticks_per_second

        ticks_since_last = self.ticks - self.last_ticks
        this_tps = float(ticks_since_last) / time_since_last
        self.smoothing_history.append(this_tps)

        # Calculate average ticks per second over the smoothing history
        self.ticks_per_second = sum(self.smoothing_history) / len(self.smoothing_history)

        self.last_time = new_time
        self.last_ticks = self.ticks

        return self.ticks_per_second
    

def scan_for_conflictfiles(conflicts_queue, status_queue, search_dir):
    """Scan for conflict files in the given directory, and add them to the queue."""
    report_frequency = 10.0
    last_report = time.time()
    get_tps = TicksPerSecondWatcher()
    n_scanned = 0
    for j, (root, dirs, files) in enumerate(os.walk(search_dir)):
        for i, fp in enumerate(files):
            n_scanned += 1
            tps = get_tps()
            current_time = time.time()
            if current_time - last_report > 1./report_frequency and tps is not None:
                last_report = current_time
                report = dict(tps=tps, n_scanned=n_scanned, n_todo=len(files) - i, root=root)
                status_queue.put(report)
            if looks_like_conflictfile(fp):
                conflicts_queue.put(os.path.join(root, fp))
    # Send a final status.
    report = dict(tps=0, n_scanned=n_scanned, n_todo=0, root=None)
    status_queue.put(report)


def normalize_path(path: str) -> str:
    """
    Normalize the given path by removing the ".sync-conflict-NNNNNNNN-NNNNNN-XXXXXXX" part.

    Parameters:
    path (str): The path to normalize.

    Returns:
    str: The normalized path.
    """
    conflict_indicator = ".sync-conflict-"
    conflict_length = len(conflict_indicator) + len("NNNNNNNN-NNNNNN-XXXXXXX")

    conflict_index = path.find(conflict_indicator)
    if conflict_index == -1:
        return path

    return path[:conflict_index] + path[conflict_index + conflict_length:]


class Sibship:

    def __init__(self, path: str):
        self.paths = [path]
        self.normalized = normalize_path(path)

    def maybe_add(self, path: str) -> bool:
        if normalize_path(path) == self.normalized:
            self.paths.append(path)
            if hasattr(self, "callback"):
                self.callback(path)
            return True
        return False
    
    def get_paths_to_compare(self) -> list[str]:
        out = self.paths
        if self.normalized not in out:
            out = [self.normalized] + out
        return out
    
    @property
    def n_extant(self) -> int:
        return len([p for p in self.get_paths_to_compare() if os.path.exists(p)])


class ConflictFileListbox(ttk.Frame):

    def __init__(self, searcher, gui_root, conflicts_queue, status_queue):
        super().__init__(gui_root)
        self.searcher = searcher
        self.master = gui_root
        self.conflicts_queue = conflicts_queue
        self.status_queue = status_queue
        
        self.grid(row=0, column=0, sticky='nsew')
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_rowconfigure(0, weight=1)

        self.create_widgets()
        self.sibships = []

    def create_widgets(self):

        # self.config(borderwidth=2, relief="groove", background='bisque')


        # selectmode should be one-at-a-time.
        # self.listbox = ttk.Listbox(self, selectmode=ttk.SINGLE, width=200, height=50)
        self.box_for_treeview = ttk.Frame(self)

        self.box_for_treeview.grid(row=0, column=0, sticky='nsew')

        # self.box_for_treeview.pack(side=ttk.LEFT, fill=ttk.BOTH, expand=True)
        # self.box_for_treeview.pack_propagate(False)
        self.treeview = ttk.Treeview(
            self.box_for_treeview,
            columns=(
                'basename', 'hash', 
                "num_files", "size",
                'modified'
            ),
            show="tree headings",
            selectmode="browse",
        )

        
        self.treeview.heading("basename", text="Basename")
        self.treeview.heading("hash", text="Hash")
        self.treeview.heading("num_files", text="#")
        self.treeview.heading("size", text="Size [b]")
        self.treeview.heading("modified", text="Modified")

        self.treeview.column("#0", width=700)
        self.treeview.column("basename", width=150)
        self.treeview.column("hash", width=40)
        self.treeview.column("num_files", width=1)
        self.treeview.column("size", width=10)
        self.treeview.column("modified", width=10)
        
        self.treeview.bind("<ButtonRelease-1>", self.expand)
        self.treeview.bind("<Double-Button-1>", self.open_item)

        # self.listbox.column('basepath', width=200, anchor='w')
        scrollbar = ttk.Scrollbar(self.box_for_treeview, orient="vertical")
        scrollbar.config(command=self.treeview.yview)
        self.treeview.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.treeview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Make a frame for vertical stacking of buttons.
        self.button_frame = ttk.Frame(self)
        # self.button_frame.pack(side=ttk.LEFT, fill=tk.Y)
        self.button_frame.grid(row=0, column=1, sticky='nsew')
    
        # Add a button to compare the selected sibship.
        self.button = ttk.Button(self.button_frame)
        self.button["text"] = "Compare selected sibship"
        self.button["command"] = self.compare_selected_sibship
        self.button.pack()

        # Add a button to open the directory of the selected sibship.
        self.open_button = ttk.Button(self.button_frame)
        self.open_button["text"] = "Open directory of selected sibship"
        self.open_button["command"] = self.open_directory_of_selected_sibship
        self.open_button.pack()

        # Add a button to rescan the directory.
        self.rescan_button = ttk.Button(self.button_frame)
        self.rescan_button["text"] = "Rescan directory"
        self.rescan_button["command"] = self.rescan_directory
        self.rescan_button.pack()

        # Add a button to delete a file (with confirmation).
        self.delete_button = ttk.Button(self.button_frame)
        self.delete_button["text"] = "Trash selected file"
        self.delete_button["command"] = self.delete_selected_file
        self.delete_button.pack()

        # A field for count of files scanned.
        self.n_scanned = tk.StringVar()
        self.n_scanned.set("Files scanned: 0")
        self.n_scanned_label = ttk.Label(self.button_frame, textvariable=self.n_scanned)
        self.n_scanned_label.pack()

        # A field in which to display scans-per-second info.
        self.scans_per_second = tk.StringVar()
        self.scans_per_second.set("Scans per second: 0")
        self.scans_per_second_label = ttk.Label(self.button_frame, textvariable=self.scans_per_second)
        self.scans_per_second_label.pack()

        # A field for the currently scanning directory root.
        self.scanning_root = tk.StringVar()
        self.scanning_root.set("Scanning: ")
        self.scanning_root_label = ttk.Label(self.button_frame, textvariable=self.scanning_root)
        self.scanning_root_label.pack()

        # A field for the number recently discovered still todo.
        self.n_todo = tk.StringVar()
        self.n_todo.set("Files this folder still to scan: 0")
        self.n_todo_label = ttk.Label(self.button_frame, textvariable=self.n_todo)
        self.n_todo_label.pack()

        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

    def delete_selected_file(self):
        # Confirm the deletion.
        selected = self.treeview.selection()[0]
        sibship = self.find_sibship(selected)
        do_delete = tk.messagebox.askyesno("Trash file", "Are you sure you want to send the selected file to trash:\n" + selected)
        if do_delete:
            selected_demangled = self._demangle(selected)
            if selected_demangled in sibship.paths:
                sibship.paths.remove(selected_demangled)
            # os.remove(selected_demangled)
            send2trash.send2trash(selected_demangled)
            self.treeview.delete(selected)

            if len(sibship.get_paths_to_compare()) <= 1:
                # Remove all the items in the sibship from the treeview.
                for p in sibship.get_paths_to_compare():
                    self.treeview.delete(p)

    def rescan_directory(self):
        self.searcher.restarted = True

        # Kill self.
        self.master.destroy()
        self.master.quit()

    def find_sibship(self, path):
        for sib in self.sibships:
            if sib.normalized == normalize_path(path):
                return sib
        return None

    def compare_selected_sibship(self, event=None):
        selected_item = self.treeview.selection()[0]
        selected_item = self._demangle(selected_item)
        selected_sibship = self.find_sibship(selected_item)
        selected_files = selected_sibship.get_paths_to_compare()
        print("Comparing:")
        for f in selected_files:
            print(f)
        # Launch meld with the selected files.
        # Windows:
        if os.name == "nt":
            subprocess.Popen(["Meld.exe"] + selected_files)
        # Linux:
        else:
            assert os.name == "posix"
            for_comparison = selected_files[:3]
            print("$ meld " + " ".join([f'"{s}"' for s in for_comparison]))
            subprocess.Popen(["meld"] + for_comparison)

    def expand(self, event=None):
        assert len(self.treeview.selection()) == 1
        selected_item_raw = self.treeview.selection()[0]
        t = time.time()
        n_children = len(self.treeview.get_children(selected_item_raw))
        if n_children > 0:
            # expand/contract the node.
            before = self.treeview.item(selected_item_raw, "open")
            if before:
                self.treeview.item(selected_item_raw, open=False)
            else:
                self.treeview.item(selected_item_raw, open=True)
            self.treeview.update_idletasks()

    def open_directory_of_selected_sibship(self, event=None):
        selected_item_raw = self.treeview.selection()[0]
        n_children = len(self.treeview.get_children(selected_item_raw))
        if n_children == 0:
            selected_item = self._demangle(selected_item_raw)
            show_in_file_manager(selected_item)

    def open_item(self, event=None):
        selected_item = self.treeview.selection()[0]
        n_children = len(self.treeview.get_children(selected_item))
        if n_children == 0:
            selected_item = self._demangle(selected_item)
            # If windows
            if os.name == "nt":
                os.startfile(selected_item)
            # If linux
            else:
                try:
                    result_output = subprocess.check_output(["xdg-open", selected_item], stderr=subprocess.STDOUT).decode('utf-8')
                    # convert bytes to 
                except (FileNotFoundError, subprocess.CalledProcessError):
                    self.open_directory_of_selected_sibship()
                    result_output = ''
                if 'No application is registered as handling this file' in result_output:
                    self.open_directory_of_selected_sibship()

    @property
    def _mangling(self):
        return ' (base path)'
            
    def _mangle(self, path):
        return path + self._mangling

    def _demangle(self, path, check_existence=True):
        if path.endswith(self._mangling):
            demangled = path[:-len(self._mangling)]
            if check_existence:
                if os.path.exists(demangled):
                    return demangled
                else:
                    return path
            else:
                return demangled
        else:
            return path

    def update(self):

        # Is there a status update?
        if not self.status_queue.empty():
            status = self.status_queue.get(block=False)
            
            tps = status['tps']
            if tps is not None and tps > 0:
                self.scans_per_second.set(f"Scans per second: {tps:.0f}")
            else:
                self.scans_per_second.set("")
            n_scanned = status['n_scanned']
            self.n_scanned.set(f"Files scanned: {n_scanned}")
            if status['n_todo'] is not None and status['n_todo'] > 0:
                self.n_todo.set(f"Files this folder still to scan: {status['n_todo']}")
            else:
                self.n_todo.set("")
            if status['root'] is not None:
                nmax = 16
                scanning_root = status['root'][-nmax:]
                if len(status['root']) > nmax:
                    scanning_root = '...' + scanning_root
                self.scanning_root.set(f"Scanning: {scanning_root}")
            else:
                self.scanning_root.set("")

        # Is there a new conflict file?
        if not self.conflicts_queue.empty():

            new_path = self.conflicts_queue.get(block=False)


            added = False
            for sib in self.sibships:
                added = sib.maybe_add(new_path)
                if added:
                    break
            if not added:
                self.sibships.append(Sibship(new_path))
                sib = self.sibships[-1]
                def callback(path, path_bn=None):
                    txt = sib.normalized
                    if not self.treeview.exists(path):
                        if path_bn is None:
                            path_bn = os.path.basename(path)
                        path_dm = self._demangle(path)
                        size = os.path.getsize(path_dm)
                        if size < 10*1024:
                            with open(path_dm, 'rb') as f:
                                hash = hashlib.md5(f.read()).hexdigest()
                        else:
                            hash = ''
                        modtime = os.path.getmtime(path_dm)

                        # Convert the modtime to a human-readable string YYYY-MM-DD HH:MM:SS
                        modtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(modtime))

                        self.treeview.insert(parent=txt, text=path, values=(path_bn, hash, '', size, modtime), index=tk.END, iid=path, open=False)
                    
                    # Update the toplevel data.
                    txt_bn = os.path.basename(txt)
                    n = sib.n_extant

                    sizes = [self.treeview.item(item, 'values')[3] for item in self.treeview.get_children(txt)]
                    sizes_unique = len(set(sizes))
                    size_label = 'SAME: ' + sizes[0] if sizes_unique == 1 else ''#f"{sizes_unique} unique sizes"

                    hashes = [self.treeview.item(item, 'values')[1] for item in self.treeview.get_children(txt)]
                    hashes_unique = len(set(hashes))
                    hash_unk = len(set(hashes)) == 1 and '' in set(hashes)
                    hash_label = '' if hash_unk else ('SAME: ' + hashes[0] if hashes_unique == 1 else '')#f"{hashes_unique} unique hashes"

                    self.treeview.item(txt, values=(txt_bn, hash_label, n, size_label, ''))
                    
                txt = sib.normalized
                sib.callback = callback
                txt_bn = os.path.basename(txt)
                self.treeview.insert(parent='', text=txt, values=(txt_bn, '', sib.n_extant, '', ''), index=tk.END, iid=txt, open=False)
                if os.path.exists(txt):
                    callback(self._mangle(txt), txt_bn)
                callback(new_path)

        self.after(100, self.update)


class Scanner:

    def __init__(self, search_dir):
        self.search_dir = search_dir
        self.restarted = False
        self.start()

    def start(self):
        while True:
            # Start searching.
            conflicts_queue = queue.Queue()
            status_queue = queue.Queue()
            t = threading.Thread(target=scan_for_conflictfiles, args=(conflicts_queue, status_queue, self.search_dir))
            t.start()

            # Start the UI.
            root = tk.Tk(className='scanForSyncthingConflicts')
            root.geometry("2000x1400")
            
            # Create the listbox.
            app = ConflictFileListbox(self, root, conflicts_queue, status_queue)
            app.update()

            if use_sv:
                sv_ttk.set_theme('light')

            # Start the main loop.
            root.mainloop()

            # Kill the searching thread.
            t.join()

            if self.restarted:
                self.restarted = False  # Allow the exit button to actually work.
                continue
            else:
                break


def main():

    # Ask the user for the SEARCH_DIR using a directory selection dialog.
    import tkinter.filedialog
    default_search_dir = os.path.join(os.path.expanduser("~"), 'Dropbox')#, 'Projects', 'Journal')
    SEARCH_DIR = tkinter.filedialog.askdirectory(title="Select the top-level directory to search for conflict files.", initialdir=default_search_dir)
    SEARCH_DIR = os.path.normpath(SEARCH_DIR)

    Scanner(SEARCH_DIR)


if __name__ == "__main__":
    main()
