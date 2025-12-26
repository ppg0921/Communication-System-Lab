function licFlag = checkCodegenLicense
%checkCodegenLicense Check for MATLAB Coder license
%   L = checkCodegenLicense returns true if MATLAB Coder is installed and a
%   valid license is available. Otherwise, this function returns false.

%   Copyright 2015-2019 The MathWorks, Inc.

% Check for MATLAB Coder
licFlag = istbxinstalled('matlabcoder','coder/matlabcoder') && ...
    istbxlicensed('MATLAB_Coder');
