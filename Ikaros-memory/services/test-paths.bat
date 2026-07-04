@echo off
call "%~dp0..\..\Ikaros-environment\ikaros-env.bat"
echo IKAROS_LLAMA_SERVER=%IKAROS_LLAMA_SERVER%
echo IKAROS_MODEL_EMBEDDING=%IKAROS_MODEL_EMBEDDING%
echo LLAMA exists: & if exist "%IKAROS_LLAMA_SERVER%" (echo YES) else (echo NO)
echo MODEL exists: & if exist "%IKAROS_MODEL_EMBEDDING%" (echo YES) else (echo NO)
