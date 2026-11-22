#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================"
echo "  REKORDBOX VAULT - Publicar e Gerar Build no GitHub"
echo "  Repositorio: https://github.com/DyFuchs/rb-vault"
echo "============================================================"
echo ""

if ! command -v git >/dev/null 2>&1; then
  echo "[ERRO] Git nao encontrado. Instale o Xcode Command Line Tools (xcode-select --install)."
  read -r -p "Pressione Enter para fechar..."
  exit 1
fi

if [ ! -f ".github/workflows/build.yml" ]; then
  echo "[ERRO] Nao encontrei .github/workflows/build.yml nesta pasta."
  read -r -p "Pressione Enter para fechar..."
  exit 1
fi

read -r -p "Digite a versao da tag e aperte Enter [padrao: v0.3.2]: " VER
VER="${VER:-v0.3.2}"
[[ "$VER" != v* ]] && VER="v$VER"

echo "[1/5] Preparando repositorio Git local..."
[ ! -d ".git" ] && git init
git branch -M main
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin https://github.com/DyFuchs/rb-vault.git
else
  git remote add origin https://github.com/DyFuchs/rb-vault.git
fi
git config user.name >/dev/null 2>&1 || git config user.name "DyFuchs"
git config user.email >/dev/null 2>&1 || git config user.email "DyFuchs@users.noreply.github.com"
git merge --abort >/dev/null 2>&1 || true

echo "[2/5] Sincronizando historico com o GitHub..."
if git fetch origin main >/dev/null 2>&1; then
  git reset --soft origin/main >/dev/null 2>&1 || true
fi

echo "[3/5] Adicionando arquivos e criando commit..."
git add -A
git commit -m "Rekordbox Vault $VER" || echo "[INFO] Nenhuma alteracao nova para commitar."

echo "[4/5] Enviando codigo para branch main..."
git push -u origin main || git push -u -f origin main

echo "[5/5] Criando e enviando tag $VER..."
git tag -f "$VER"
git push -f origin "$VER"

echo ""
echo "SUCESSO! Tag $VER enviada para DyFuchs/rb-vault!"
open "https://github.com/DyFuchs/rb-vault/actions"
read -r -p "Pressione Enter para fechar..."
