' Lance refresh-l2.bat sans fenetre visible (pour la tache planifiee)
Set sh = CreateObject("WScript.Shell")
root = "C:\Users\Youss\Documents\animations youtube"
sh.CurrentDirectory = root
sh.Run "cmd /c """ & root & "\refresh-l2.bat""", 0, False
