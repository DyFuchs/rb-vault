@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Publicar Rekordbox Vault no GitHub - DyFuchs/rb-vault
cd /d "%~dp0"

echo ============================================================
echo   REKORDBOX VAULT - Publicar e Gerar Build no GitHub
echo   Repositorio: https://github.com/DyFuchs/rb-vault
echo ============================================================
echo.

rem Procurar Git no PATH ou em pastas padrao do Windows
where git >nul 2>nul
if errorlevel 1 if exist "C:\Program Files\Git\cmd\git.exe" set "PATH=C:\Program Files\Git\cmd;!PATH!"
where git >nul 2>nul
if errorlevel 1 if exist "C:\Program Files (x86)\Git\cmd\git.exe" set "PATH=C:\Program Files (x86)\Git\cmd;!PATH!"
where git >nul 2>nul
if errorlevel 1 if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;!PATH!"

where git >nul 2>nul
if errorlevel 1 goto :no_git

rem Se o script estiver um nivel acima da pasta extraida, entrar nela automaticamente
if not exist ".github\workflows\build.yml" (
    for /d %%D in (*) do (
        if exist "%%D\.github\workflows\build.yml" (
            echo [INFO] Pasta do kit encontrada em: %%D
            cd /d "%%D"
        )
    )
)

if not exist ".github\workflows\build.yml" goto :no_kit

set "VER="
set /p VER="Digite a versao da tag e aperte Enter [padrao: v0.3.3]: "
if "!VER!"=="" set "VER=v0.3.3"
if /i not "!VER:~0,1!"=="v" set "VER=v!VER!"

echo.
echo [1/5] Preparando repositorio Git local...
if not exist ".git" git init
git branch -M main

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    git remote add origin https://github.com/DyFuchs/rb-vault.git
) else (
    git remote set-url origin https://github.com/DyFuchs/rb-vault.git
)

git config user.name >nul 2>nul
if errorlevel 1 git config user.name "DyFuchs"
git config user.email >nul 2>nul
if errorlevel 1 git config user.email "DyFuchs@users.noreply.github.com"

rem Limpar eventual merge pendente de tentativa anterior
git merge --abort >nul 2>nul

echo.
echo [2/5] Sincronizando historico com o GitHub - pode abrir login na 1a vez...
git fetch origin main >nul 2>nul
if not errorlevel 1 git reset --soft origin/main >nul 2>nul

echo.
echo [3/5] Adicionando arquivos da nova versao e criando commit...
git add -A
git commit -m "Rekordbox Vault !VER!"
if errorlevel 1 echo [INFO] Nenhuma alteracao nova para commitar. Seguindo com push e tag...

echo.
echo [4/5] Enviando codigo para branch main...
git push -u origin main
if errorlevel 1 (
    echo [INFO] Sincronizando branch main com a nova pasta do kit...
    git push -u -f origin main
    if errorlevel 1 goto :err_push
)

echo.
echo [5/5] Criando e enviando tag !VER! para disparar os builds...
git tag -f "!VER!"
git push -f origin "!VER!"
if errorlevel 1 goto :err_tag

echo.
echo ============================================================
echo   SUCESSO! Tag !VER! enviada para DyFuchs/rb-vault!
echo   Abrindo a aba Actions para acompanhar a compilacao...
echo ============================================================
echo.
start "" "https://github.com/DyFuchs/rb-vault/actions"
pause
endlocal
exit /b 0

:no_git
echo [ERRO] O Git nao esta instalado neste Windows.
echo Baixe em: https://git-scm.com/download/win
echo Depois de instalar, execute este script novamente.
echo.
pause
endlocal
exit /b 1

:no_kit
echo [ERRO] Nao encontrei ".github\workflows\build.yml" nesta pasta.
echo Pasta atual: !CD!
echo.
echo Coloque este script DENTRO da pasta extraida do GitHub Kit,
echo onde ficam as pastas "app", "build" e ".github".
echo.
pause
endlocal
exit /b 1

:err_push
echo.
echo [ERRO] Falha ao enviar para o GitHub.
echo Verifique se concluiu o login na janela do GitHub e tente novamente.
echo.
pause
endlocal
exit /b 1

:err_tag
echo.
echo [ERRO] Falha ao enviar a tag !VER!.
echo.
pause
endlocal
exit /b 1
