clear; 
l = 1e3 ; 
rx_l = l ; 
[radio_Tx,radio_Rx] = USRP_init(rx_l) ; 

sr = 1e3 ; 
t = linspace(0,l/sr,l) ; 
n = 50 ; 
f_range = linspace(1,10,n) ; 


buffer = zeros(n+30,rx_l) ;

f =1 ; 

% Transmit and receive for 50 frames 
for ii =1 : n
    f = f_range(ii); 
    data = 1*sin(2*pi*f*t).' ;  
    tunderrun = radio_Tx(data);
    [rcvdSignal, ~, toverflow] = step(radio_Rx);
    buffer(ii,:) = real(rcvdSignal.' ); 
end 

% Keep reception for 30 frames 
for ii = n+1 : n+30
    [rcvdSignal, ~, toverflow] = step(radio_Rx);
    buffer(ii,:) = real(rcvdSignal.' ); 
end 



release(radio_Tx)
release(radio_Rx)

f_buffer = zeros(1,n) ; 

for ii = 1 : n+30
    f_buffer(ii) = find_f( sr,buffer(ii,:) ) ; 
end 

figure ;
plot(f_buffer)
title(sprintf("Main Frequency component of each frame with frame length = %d",l))
xlabel("Frame index")
ylabel("Frequency")
savefig(sprintf("fig/length_%d",l));
saveas(gcf,sprintf("fig/length_%d.png",l))

% Plot the received data of the 50-th frame 
figure ;
plot(buffer(50,:))