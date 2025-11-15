// Функция для сохранения связи между чатом и ссылкой
function saveChatMapping(chatUserId, linkCode) {
    fetch('https://roomix-production.up.railway.app/save_chat_mapping', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            chat_user_id: chatUserId,
            link_code: linkCode
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            console.log('✅ Связь чата сохранена:', chatUserId, '->', linkCode);
        } else {
            console.error('❌ Ошибка сохранения связи:', data.error);
        }
    })
    .catch(error => {
        console.error('❌ Ошибка запроса:', error);
    });
}

// Обнови функцию loadLinkData чтобы сохранять связь
function loadLinkData() {
    const linkCode = getLinkCode();
    
    if (linkCode) {
        console.log('🔗 Загружаем данные по коду:', linkCode);
        
        // Сохраняем связь между user_id чата и кодом ссылки
        const chatUserId = supportChat.userId;
        saveChatMapping(chatUserId, linkCode);
        
        loadDataFromAPI(linkCode);
    } else {
        console.log('ℹ️ Код не найден, используем стандартные данные');
        updateRoomDisplay();
    }
}
