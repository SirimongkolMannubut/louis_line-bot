import customtkinter as ctk
from ui.voice_chat import LouisAI

# Initialize appearance mode and color theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

root = ctk.CTk()
app = LouisAI(root)
root.mainloop()
