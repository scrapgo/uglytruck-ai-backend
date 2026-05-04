import os
import shutil

def clean_folder(folder_path: str):
    """Remove all files and subfolders from the given directory."""
    if os.path.exists(folder_path):
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)  # remove file or symlink
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)  # remove folder
            except Exception as e:
                print(f"⚠️ Failed to delete {file_path}: {e}")
