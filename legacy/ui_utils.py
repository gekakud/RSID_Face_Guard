try:
    import tkinter as tk
    import tkinter.ttk as ttk
    from tkinter import messagebox, simpledialog
except ImportError as ex:
    print(f'Failed importing tkinter ({ex}).')
    print('  On Ubuntu, you also need to: sudo apt install python3-tk')
    print('  On Fedora, you also need to: sudo dnf install python3-tkinter')
    exit(1)
    

class EnrollDialog(tk.Toplevel):
    """Custom dialog for enrolling users with additional fields"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.result = None
        
        self.title("Enroll New User")
        self.geometry("400x250")
        self.resizable(False, False)
        
        # Center the dialog
        self.transient(parent)
        self.grab_set()
        
        # Create form fields
        tk.Label(self, text="User ID:").grid(row=0, column=0, padx=10, pady=10, sticky='e')
        self.id_entry = tk.Entry(self, width=30)
        self.id_entry.grid(row=0, column=1, padx=10, pady=10)
        
        tk.Label(self, text="Name:").grid(row=1, column=0, padx=10, pady=10, sticky='e')
        self.name_entry = tk.Entry(self, width=30)
        self.name_entry.grid(row=1, column=1, padx=10, pady=10)
        
        tk.Label(self, text="Permission Level:").grid(row=2, column=0, padx=10, pady=10, sticky='e')
        self.permission_var = tk.StringVar(value="Limited access")
        self.permission_combo = ttk.Combobox(self, textvariable=self.permission_var, width=27, state='readonly')
        self.permission_combo['values'] = ('Extended access', 'Limited access')
        self.permission_combo.grid(row=2, column=1, padx=10, pady=10)
        
        # Buttons
        button_frame = tk.Frame(self)
        button_frame.grid(row=3, column=0, columnspan=2, pady=20)
        
        tk.Button(button_frame, text="OK", command=self.ok_pressed, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Cancel", command=self.cancel_pressed, width=10).pack(side=tk.LEFT, padx=5)
        
        # Focus on first field
        self.id_entry.focus_set()
        
        # Bind Enter key
        self.bind('<Return>', lambda e: self.ok_pressed())
        self.bind('<Escape>', lambda e: self.cancel_pressed())
    
    def ok_pressed(self):
        user_id = self.id_entry.get().strip()
        name = self.name_entry.get().strip()
        permission = self.permission_var.get()
        
        if not user_id:
            messagebox.showerror("Error", "User ID is required!", parent=self)
            return
        
        if not name:
            messagebox.showerror("Error", "Name is required!", parent=self)
            return
        
        self.result = {
            'id': user_id,
            'name': name,
            'permission_level': permission
        }
        self.destroy()
    
    def cancel_pressed(self):
        self.result = None
        self.destroy()