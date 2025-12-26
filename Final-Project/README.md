## Repository Structure

This repository contains Python and MATLAB code for ADS-B signal processing, analysis, and visualization.  
The MATLAB code is modified from the MathWorks example for receiving ADS-B signals:  
[Airplane Tracking Using ADS-B Signals](https://www.mathworks.com/help/comm/ug/airplane-tracking-using-ads-b-signals.html)

### Demo_Data/
Contains the datasets used by the Python scripts in this repository.

### Python Scripts
- **process.py**  
  Plots received ADS-B signals under different receiver gain configurations.

- **trail.py**  
  Plots data captured on the demo day.

### Morris_Matlab/
*Author: Zhan-Rui (Morris) Wang, Dai-En Liu*  
Contains MATLAB sample code (with modification) and an implementation of Doppler shift measurement.
- Adds an energy threshold to `helperAdsbRxPhyBitParser.m`.
- **plot_packet.m**
  Plots the first received correctly and incorrectly decoded packets.

### SNR_RSS_analysis/
*Author: Chun-Yun (Betty) Cheng*  
Contains tools for signal quality analysis:
- MATLAB code for generating log files with SNR and RSS information.
- Python code for analyzing the generated log files:
  - **adsb-log-analysis.py**  
    Plots distance, elevation angle, and altitude distributions of aircraft from which packets are received.
  - **adsb-snr-analysis.py**  
    Plots SNR distributions for successful and failed packets, as well as SNR versus distance, elevation angle, and altitude.
  - **adsb-rss-analysis.py**  
    Plots RSS distributions for successful and failed packets, as well as RSS versus distance, elevation angle, and altitude.
- Commands used to run the scripts are documented in the corresponding `.sh` files.
