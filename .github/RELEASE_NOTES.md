## Install the GNOME extension

The extension is distributed through GitHub Releases. Install the `aiu` CLI first:

```bash
uv tool install git+https://github.com/citizenu03bb/aiu
```

Or use `pipx install git+https://github.com/citizenu03bb/aiu`.

Download `aiu-indicator@citizenu03bb.github.io.shell-extension.zip` from this
release's **Assets**. In the directory containing the downloaded ZIP, run:

```bash
gnome-extensions install --force aiu-indicator@citizenu03bb.github.io.shell-extension.zip
```

Log out and back in, then enable the extension:

```bash
gnome-extensions enable aiu-indicator@citizenu03bb.github.io
```

To check the download, also download `release.sha256` into the same directory
and run `sha256sum --check release.sha256` before installing.

## Update

Download the new ZIP, repeat the install command above, then log out and back
in. Extension updates are manual; they are not delivered through
extensions.gnome.org. Update the CLI separately with `uv tool upgrade aiu`
or `pipx upgrade aiu`, using the tool you installed it with.

[Provider setup, screenshots and extension documentation](https://github.com/citizenu03bb/aiu/tree/main/gnome-extension#readme).
