load("Lab1_TXSig.mat");
%% (1)
figure;
plot(real(Sym_TX), imag(Sym_TX), '.', "MarkerSize", 12);
grid on;
xlabel('I')
ylabel('Q')
title('(1) Constellation diagram of Sym_TX')
% xline(0, 'k-', 'LineWidth', 2); % vertical axis at x=0
% yline(0, 'k-', 'LineWidth', 2); % horizontal axis at y=0

%% (2)
N1 = 32;
N2 = N1*(1000/5);
Fs1 = 5;
Fs2 = 1000;
X_sym = fftshift(fft(Sym_TX, N1));
X_sig_upsamp = fftshift(fft(Sig_BB_TX_Upsamp, N2));
f1  = (-N1/2:N1/2-1)*(Fs1/N1);
f2 = (-N2/2:N2/2-1)*(Fs2/N2);

figure; hold on; grid on;
plot(f2, abs(X_sig_upsamp), 'LineWidth', 1.2);
plot(f1, abs(X_sym), 'LineWidth', 1.2);

xlim([-500 500]);
xlabel('Frequency (Hz)');
ylabel('Magnitude');
title('Magnitude Spectra (fftshifted): Sym\_TX vs Sig\_BB\_TX\_Upsamp');
legend('|FFT\{Sig\_BB\_TX\_Upsamp\}| (Fs=1000 Hz)', ...
       '|FFT\{Sym\_TX\}| (Fs=5 Hz)', ...
       'Location', 'best');

%% (3)
figure; hold on; grid on;
plot(f2, abs(X_sig_upsamp), 'LineWidth', 1.2);
plot(f1, 200*abs(X_sym), 'LineWidth', 1.2);
xlim([-5 5]);
xlabel('Frequency (Hz)'); ylabel('Magnitude');
title('Zoomed: ±5 Hz');
legend('|FFT{Sig\_BB\_TX\_Upsamp}|', '|FFT{Sym\_TX}|', 'Location', 'best');

%% (4)
N = 6400;
Fs = 1000;
fc = 50;
Sig_upconv = Sig_BB_TX_Upsamp .* exp(1j*2*pi*fc*tSamp);
Sig_upconv_real = real(Sig_upconv);

f = (-N/2:N/2-1)*(Fs/N);
X_sig_baseband = fftshift(fft(Sig_BB_TX_Upsamp));
X_sig_upconv = fftshift(fft(Sig_upconv));

figure; hold on; grid on;
plot(f, abs(X_sig_baseband), 'LineWidth', 1.2);
plot(f, abs(X_sig_upconv), 'LineWidth', 1.2);
xlim([-100 100]);
xlabel('Frequency (Hz)');
ylabel('Magnitude');
title('Magnitude Spectrum of Baseband vs Upconverted Signals');
legend('|FFT{Baseband}|', '|FFT{Upconverted (real)|', 'Location', 'best');