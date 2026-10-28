# Third-party notices

This list is my best understanding and must be verified before you distribute binaries.

* **Qt for Python (PySide6-Essentials, shiboken6)**: available under LGPLv3/GPL/commercial terms from The Qt Company.
  The Essentials wheel is distributed under LGPLv3. If you redistribute a bundle, you must comply with LGPLv3
  (provide the license text, state that Qt is used, and keep the Qt libraries replaceable; the PyInstaller one-folder
  build ships them as separate libraries, the one-file build is harder to replace and needs legal review).
* **Python**: Python Software Foundation License.
* **PyInstaller** (build tool): GPLv2 with a bootloader exception that permits bundling non-GPL applications.
* **Test/dev tools** (pytest, ruff, pip-audit, pip-tools): not shipped.
* No dictionaries or language data are bundled. The bundled sample text (`src/wpl/lab/sample_text.txt`) was written for this project.
* The project's own license has not been chosen yet. Add a `LICENSE` file and re-enable `LicenseFile` in
  `packaging/installer.iss`.
