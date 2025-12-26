% In the MATLAB Command Window, run this script:

% --- 1. Get the data from the persistent memory (if necessary, if the function is still active) ---
% NOTE: If you are in a separate function, you may need a small helper to access the persistent vars.
% For simplicity, we assume you run this in the command window or a script that can see the data.

% --- 2. Define Parameters for Plotting ---
% Assuming an Extended Squitter packet (112 us duration)
Fs_processed = 20e6; % 12 MHz processing rate
packet_length = length(correctlyDecodedPacket);
t_us = (0:packet_length-1) * (1/Fs_processed) * 1e6; % Time in microseconds (us)

% --- 3. Create the Comparison Plot ---
figure('Name', 'Packet Waveform Comparison (Post-Sync)');

subplot(2,1,1);
plot(t_us, correctlyDecodedPacket);
title('1. Correctly Decoded Packet (CRC Pass)');
xlabel('Time (\mus)');
ylabel('Amplitude (abs(z))');
grid on;

subplot(2,1,2);
plot(t_us, incorrectlyDecodedPacket);
title('2. Incorrectly Decoded Packet (CRC Fail)');
xlabel('Time (\mus)');
ylabel('Amplitude (abs(z))');
grid on;

linkaxes; % Link the X and Y axes for easy comparison

P_correct = mean(abs(correctlyDecodedPacket).^2) * 1e3;
fprintf('Power of the first correctly decoded packet is %.4f.\n', P_correct);
P_incorrect = mean(abs(incorrectlyDecodedPacket).^2) * 1e3;
fprintf('Power of the first incorrectly decoded packet is %.4f.\n', P_incorrect);

detector = [ones(10, 1); -ones(10, 1)];
packet = reshape(incorrectlyDecodedPacket(1:112*20), 20, 112);
demod_packet = double(sum(detector .* packet, 1) >= 0);
key = [1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 0 0 0 0 0 1 0 0 1];
[q, r] = gfdeconv(flip(demod_packet), flip(key));
q
r