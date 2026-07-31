@echo off
REM ============================================================
REM  Rafraichissement auto du recap mercato Ligue 2
REM  - scrape Transfermarkt -> regenere index.html + equipe.html
REM    directement dans le repo dedie "mercatol2"
REM  - publie sur GitHub Pages uniquement si les DONNEES ont change
REM ============================================================
setlocal
set "PUSH=1"
set "REPO=C:\Users\Youss\Documents\mercato-l2-repo"
set "SRC=C:\Users\Youss\Documents\animations youtube"

cd /d "%SRC%"
echo [%date% %time%] Scrape mercato L2...
python "scripts\scrape_l2_transfers.py"
if errorlevel 1 (
  echo ECHEC du scrape, publication annulee.
  exit /b 1
)

if not "%PUSH%"=="1" goto :done

set "CHANGED=0"
if exist "%REPO%\data\.push" set /p CHANGED=<"%REPO%\data\.push"
if not "%CHANGED%"=="1" (
  echo Donnees inchangees, pas de publication.
  goto :done
)

pushd "%REPO%"
git add index.html equipe.html data\transfers_l2.json logos
git commit -m "MAJ mercato L2 (auto)"
git push
echo Publie sur GitHub Pages (mercatol2).
popd

:done
endlocal
