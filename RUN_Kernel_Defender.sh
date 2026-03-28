#!/bin/bash
echo "============================================="
echo "   KERNEL DEFENDER - PRODUCTION EXECUTABLE   "
echo "============================================="
echo "Acest script inlocuieste instalarea complexa "
echo "a unui binar pur, asigurand zero erori de    "
echo "subprocess sau pathing la fata locului!      "
echo ""

# Mergem in folderul sursă
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR/Kernel Defender"

# Executăm siguri environmentul local
python3 Kernel_Defender.py
