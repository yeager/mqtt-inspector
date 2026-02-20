# MQTT Inspector

[![Version](https://img.shields.io/badge/version-0.2.0-blue)](https://github.com/yeager/mqtt-inspector/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%203.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Transifex](https://img.shields.io/badge/Transifex-Translate-green.svg)](https://www.transifex.com/danielnylander/mqtt-inspector/)

MQTT message inspector for IoT debugging — GTK4/Adwaita.

![Screenshot](screenshots/main.png)

## Features

- **Live messages** — subscribe and watch MQTT topics in real time
- **Topic browser** — tree view of all discovered topics
- **Message history** — scrollback with timestamps
- **Publish** — send messages to any topic
- **JSON formatting** — auto-format JSON payloads
- **Multiple brokers** — save and switch between connections
- **Dark/light theme** toggle

## Installation

### Debian/Ubuntu

```bash
echo "deb [signed-by=/usr/share/keyrings/yeager-keyring.gpg] https://yeager.github.io/debian-repo stable main" | sudo tee /etc/apt/sources.list.d/yeager.list
curl -fsSL https://yeager.github.io/debian-repo/yeager-keyring.gpg | sudo tee /usr/share/keyrings/yeager-keyring.gpg > /dev/null
sudo apt update && sudo apt install mqtt-inspector
```

### Fedora/openSUSE

```bash
sudo dnf config-manager --add-repo https://yeager.github.io/rpm-repo/yeager.repo
sudo dnf install mqtt-inspector
```

### From source

```bash
git clone https://github.com/yeager/mqtt-inspector.git
cd mqtt-inspector && pip install -e .
mqtt-inspector
```

## Translation

Help translate on [Transifex](https://www.transifex.com/danielnylander/mqtt-inspector/).

## License

GPL-3.0-or-later — see [LICENSE](LICENSE) for details.

## Author

**Daniel Nylander** — [danielnylander.se](https://danielnylander.se)
