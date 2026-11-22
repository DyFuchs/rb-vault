#!/bin/bash
set -u
cd "$(dirname "$0")"

echo "============================================================"
echo "  REKORDBOX VAULT - Publicar e Gerar Build no GitHub (Mac)"
echo "  Repositorio: https://github.com/DyFuchs/rb-vault"
echo "============================================================"
echo ""

if ! command -v git >/dev/null 2>&1; then
    echo "[ERRO] Git nao encontrado. Instale com: xcode-select --install"
    read -r -p "Pressione Enter para fechar..."
    exit 1
fi

if [ ! -f ".github/workflows/build.yml" ]; then
    echo "[AVISO] Nao encontrei .github/workflows/build.yml nesta pasta!"
    echo "Coloque este script DENTRO da pasta extraida do GitHub Kit (onde estao 'app' e '.github')."
    read -r -p "Pressione Enter para fechar..."
    exit 1
fi

read -r -p "Digite a versao da tag (pressione Enter para v0.3.1): " VER
VER="${VER:-v0.3.1}"
case "$VER" in
    v*|V*) ;;
    *) VER="v$VER" ;;
esac

echo ""
echo "[1/5] Preparando repositorio Git local..."
if [ ! -d ".git" ]; then
    git init
    git branch -M main
fi

if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin https://github.com/DyFuchs/rb-vault.git
else
    git remote add origin https://github.com/DyFuchs/rb-vault.git
fi

git config user.name >/dev/null 2>&1 || git config user.name "DyFuchs"
git config user.email >/dev/null 2>&1 || git config user.email "DyFuchs@users.noreply.github.com"

echo "[2/5] Adicionando arquivos e criando commit..."
git add -A
git commit -m "Rekordbox Vault $VER" || echo "[INFO] Nenhuma alteracao nova para commitar; seguindo..."

echo "[3/5] Sincronizando com o GitHub..."
if git fetch origin main >/dev/null 2>&1; then
    git pull origin main --allow-unrelated-histories --no-edit >/dev/null 2>&1 || true
fi

echo "[4/5] Enviando codigo para branch main..."
if ! git push -u origin main; then
    echo ""
    echo "[ERRO] Falha ao enviar para o GitHub. Verifique sua autenticacao Git e tente novamente."
    read -r -p "Pressione Enter para fechar..."
    exit 1
fi

echo "[5/5] Criando e enviando tag $VER para disparar a build (.exe + .dmg)..."
git tag -f "$VER"
if ! git push -f origin "$VER"; then
    echo "[ERRO] Falha ao enviar a tag $VER."
    read -r -p "Pressione Enter para fechar..."
    exit 1
fi

echo ""
echo "============================================================"
echo "  SUCESSO! Tag $VER enviada para DyFuchs/rb-vault!"
echo "  Abrindo a aba Actions para acompanhar a compilacao..."
echo "============================================================"
open "https://github.com/DyFuchs/rb-vault/actions"
read -r -p "Pressione Enter para fechar..."
