DIRECTIONS for MODIFIED TRANSFORMER ARCHITECTURE <!--~ Disha Shur-->
=============================================================

The following commands were used to train the models and generate th output plots shown in the plots section.

sbatch scholar.sh python -u small_lm8heads.py
sbatch scholar.sh python -u small_lm.py

For every experiment with modified architecture, there is a comparision with the original architecture. The modified architectures we tried are as follows:

-- MLP attention (Sanity check)
<br>
-- ModArch1: Using only one linear network for attention instead of an MLP network for every language model layer
<br>
-- ModArch2: Using linear network attention only for the first language model layer followed by FFN in the remaining layers
<br>
<br>
For all of the above modifications, we experiment with 128 and 256 context length size, as well as with 4 heads and 8 heads.


