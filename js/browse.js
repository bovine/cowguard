
function deleteEventButton(eventkey) {
    $.post('/events/delete', { event: eventkey }, function(data) { 
        alert(data); 
        location.reload(); 
    });
}

function markEventRead(eventkey) {
    $('#' + eventkey).removeClass('unread');
}