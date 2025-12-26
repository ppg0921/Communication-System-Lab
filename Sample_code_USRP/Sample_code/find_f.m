function f = find_f(sr,data)
    l = length(data) ; 
    f_axis = linspace(-sr/2,sr/2,l) ; 
    
        
    amp = abs(fftshift(fft(data))) ; 
    [M,I] = max(amp) ; 
    f= abs(f_axis(I)) ; 

end 