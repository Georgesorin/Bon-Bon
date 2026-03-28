@echo off
echo =============================================
echo    KERNEL DEFENDER - PRODUCTION EXECUTABLE   
echo =============================================
echo Acest program va incarca serverul Matrix local...
echo.

:: Ne asiguram ca intram in folderul corect
cd "Kernel Defender"

:: Rulam jocul via Python (pe Windows comanda este de obicei 'python' in loc de 'python3')
python Kernel_Defender.py

:: Lasam fereastra deschisa in caz de vreo eroare tehnica
pause
