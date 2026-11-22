# Rekordbox Vault v0.3.0

Exportador, importador e gerenciador de pacotes completos do **Rekordbox 6 e 7** (playlists, áudios, hot cues, beatgrids, samples, pads, preferências, efeitos e MIDI) para **Windows** e **macOS (Intel e Apple Silicon)**.

## Como gerar a build (.exe + .dmg) no GitHub (`DyFuchs/rb-vault`)

1. Extraia o conteúdo deste ZIP dentro da pasta do seu repositório `rb-vault`.
2. Suba os arquivos para o GitHub (via GitHub Desktop, site do GitHub ou terminal):
   ```bash
   git add .
   git commit -m "Rekordbox Vault v0.3.0"
   git push origin main
   ```
3. Crie a tag da versão para disparar a build e publicar o Release automaticamente:
   ```bash
   git tag v0.3.0
   git push origin v0.3.0
   ```
   *(Ou vá na aba **Actions → Build Rekordbox Vault → Run workflow** no site do GitHub.)*
4. Em ~5 a 8 minutos, os 3 instaladores aparecerão automaticamente na aba **Releases**:
   - `RekordboxVault-v0.3.0-Windows-Setup.exe`
   - `RekordboxVault-v0.3.0-macOS-Intel.dmg`
   - `RekordboxVault-v0.3.0-macOS-AppleSilicon.dmg`

## Auto-atualização

Sempre que você publicar uma tag nova (`v0.3.1`, `v0.4.0`...) em `DyFuchs/rb-vault`, o Rekordbox Vault detecta automaticamente ao abrir e permite baixar e gerenciar versões direto em **Configurações → Atualizações & Versões**.
