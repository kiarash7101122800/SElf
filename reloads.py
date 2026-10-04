def Enm():
    Enemys = []
    with open("data/Enemy.txt", "r") as file:
        enemys = file.read()
        enemys = enemys.split("\n") 
        for i in enemys:
            if i.strip(): 
                Enemys.append(int(i))
    return Enemys

def Mute():
    Mutes = []
    with open("data/Mute.txt", "r") as file:
        Mute = file.read()
        Mute = Mute.split("\n") 
        for i in Mute:
            if i.strip(): 
                Mutes.append(int(i))
    return Mutes
